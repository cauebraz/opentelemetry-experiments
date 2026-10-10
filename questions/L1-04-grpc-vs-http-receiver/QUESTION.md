---
id: L1-04
title: What does choosing gRPC over HTTP on the receiver cost or save?
level: 1
theme: collector
status: measured
---

## Question

The OTLP receiver speaks both. For the same telemetry, this question measures
the bytes that reach the Collector, the CPU the Collector and the sender spend,
and the throughput achieved. It also measures what compression and JSON
change, so the choice stops being a default and becomes a number.

## What the documentation says

> The OTLP receiver can receive trace export calls via HTTP/JSON in addition to
> gRPC. The HTTP/JSON address is the same as gRPC as the protocol is recognized
> and processed accordingly.

Source: [`receiver/otlpreceiver/README.md`, lines 55 to 57](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/receiver/otlpreceiver/README.md?plain=1#L55-L57),
`opentelemetry-collector v0.162.0`, commit
`62cdad2ea133239380b44d20d84eb26e114779b6`, which `otelcol-contrib 0.162.0`
builds on.

Line 38 of the same file gives two different defaults: "localhost:4317 for grpc
protocol, localhost:4318 http protocol".

Both checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/receiver/otlpreceiver/README.md | grep -n 'The HTTP/JSON address is the same as gRPC'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/receiver/otlpreceiver/README.md | grep -n 'localhost:4317 for grpc'
```

The README does not say what either protocol costs.

## Hypothesis

Written before the first run.

- Without compression, gRPC and HTTP protobuf deliver within 5% of each other
  in bytes at the Collector's interface, between 60 and 90 bytes per point,
  because both carry the same protobuf message.
- gzip cuts those bytes by 80% to 92% on both protocols.
- HTTP JSON without compression costs 2.5 to 4 times the bytes of HTTP
  protobuf without compression.
- HTTP JSON costs the Collector at least 1.5 times the idle-adjusted CPU per
  point of HTTP protobuf, because it parses text.
- gzip raises the Collector's CPU per point by 5% to 40% over the same
  protocol without it, because decompression is cheaper than compression.
- gzip raises the sender's CPU per point by at least 50%.
- HTTP JSON has the lowest throughput of the five variants.
- An OTLP/HTTP JSON request to the gRPC port is not answered 200, contrary to
  the README's "the HTTP/JSON address is the same as gRPC".
- Every variant delivers 100000 points to the sink.

## How to run

```bash
just ask L1-04 5
```

The sender builds the same 200 requests of 500 points as L3-06: 100000 points
of one cumulative sum, `l1_04.requests`, with `host.name` from 10 values,
`route` from 5, and a deterministic value and timestamp. Each request is one
`ExportMetricsServiceRequest`, parsed once from its OTLP JSON. The sender then
posts it in the variant's encoding, in sequence, from the harness process.

| Variant | Port | Encoding | Compression |
| --- | --- | --- | --- |
| `grpc-none` | 24317 | protobuf over gRPC | none |
| `grpc-gzip` | 24317 | protobuf over gRPC | gzip |
| `http-proto-none` | 24318 | `application/x-protobuf` | none |
| `http-proto-gzip` | 24318 | `application/x-protobuf` | `Content-Encoding: gzip` |
| `http-json-none` | 24318 | `application/json` | none |

The Collector batches at 5000 and exports to the sink over gRPC without
compression in every variant, so only the receiving side changes.

Five repetitions instead of three, with the order rotated so each variant runs
once in each position, for the same reason as L3-06: CPU on a shared Docker
Desktop VM drifts during a session.

During the window, Prometheus is stopped so no scrape reaches the Collector:
1. Idle: the counters over 5 seconds with nothing sent.
2. Workload: the counters across the 200 requests, until the sink log shows
   100000 points and the Collector's RX stops moving.

After the window, one OTLP/HTTP JSON request of one point goes to the gRPC
port, and its outcome is recorded.

## What is measured

| Field | Meaning |
| --- | --- |
| net Collector RX bytes | `eth0` RX over the window, minus the idle rate times the window |
| RX bytes per point | net RX over 100000 |
| sent bytes | the request bodies or messages as the sender wrote them |
| Collector CPU ns per point | Collector CPU minus the idle rate, over 100000 |
| sender CPU ns per point | `time.process_time` across encoding and sending, over 100000 |
| points per second | 100000 over the wall time of the 200 requests |
| JSON on the gRPC port | the HTTP status, or the error, of that one request |

A run is invalid when:
- the idle background, scaled to the window, exceeds 1% of the workload RX;
- a counter is absent or goes backwards, or a container changes;
- an interface other than `eth0` carries bytes;
- any request fails;
- the sink does not receive exactly 100000 points, or 100001 when the gRPC
  port answers the JSON request with 200;
- Prometheus does not come back.

## Result

25 valid runs, each variant once in each order position; the full table is in
[`results/REPORT.md`](results/REPORT.md). Medians, with the range across the
five repetitions:

| Variant | RX bytes per point | Collector CPU ns per point | Sender CPU ns per point | Points per second |
| --- | --- | --- | --- | --- |
| `grpc-none` | 74.0 | 822.2 (789.0-1050.2) | 526.9 (516.0-625.8) | 710363 (629376-752964) |
| `grpc-gzip` | 8.9 | 1131.7 (1070.9-1146.4) | 957.2 (873.5-1030.3) | 533919 (476173-590596) |
| `http-proto-none` | 73.9 | 836.5 (814.4-935.5) | 221.9 (205.6-245.2) | 828985 (759047-852132) |
| `http-proto-gzip` | 8.6 | 1104.0 (887.5-1138.6) | 1612.6 (1393.6-1672.0) | 385238 (373842-440790) |
| `http-json-none` | 218.5 | 1586.7 (1469.5-1682.7) | 1306.9 (1239.0-1342.2) | 329092 (314338-338539) |

Every prediction held:
- Uncompressed gRPC and HTTP protobuf differed by 0.23% in bytes, at 74.0
  and 73.9 bytes per point, inside 60 to 90.
- gzip cut the bytes by 88.0% on gRPC and 88.4% on HTTP, inside 80% to 92%.
- HTTP JSON cost 2.96 times the bytes of HTTP protobuf, inside 2.5 to 4.
- HTTP JSON cost the Collector 1.90 times the CPU per point of HTTP protobuf,
  above 1.5.
- gzip raised the Collector's CPU per point by 37.6% on gRPC and 32.0% on
  HTTP, inside 5% to 40% but near the top. The HTTP ranges overlap: the
  lowest gzip run, 887.5, sat below the highest uncompressed run, 935.5.
- gzip raised the sender's CPU per point by 81.7% on gRPC and 626.7% on HTTP,
  above 50%.
- HTTP JSON had the lowest throughput, 329092 points per second.
- All 25 JSON requests to the gRPC port failed with `BadStatusLine`, not
  HTTP 200.
- Every run delivered 100000 points in 20 export requests to the sink.

Two results were not predicted:
- Over loopback, gzip lowered throughput by 24.8% on gRPC and 53.5% on HTTP.
- Uncompressed HTTP protobuf was the fastest variant and the cheapest for the
  sender, 221.9 ns per point against gRPC's 526.9.

Background traffic on the Collector's `eth0` was 59 to 407 bytes per window.

## What this does not say

The sender is one Python process sending in sequence over loopback, so its
CPU and throughput figures describe Python's `grpcio` and `gzip` modules. They
do not describe an SDK's batching exporter, a Go client or a real network. On
a link with latency, the smaller gzip requests would weigh differently.
Collector RX is one container interface behind Docker Desktop's port
forwarding, with TCP, HTTP/2 and HTTP/1.1 framing. It is not billable
ingress, and no TLS, proxy or load balancer was run. The Collector CPU covers
receiving, batching and the uncompressed export to the sink. zstd, snappy,
concurrent senders and JSON with gzip were not run.
