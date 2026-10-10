---
id: L3-06
title: How many bytes does a datapoint cost on the wire?
level: 3
theme: collector
status: measured
---

## Question

Protocol and compression are two exporter settings with a direct line to an
egress bill. This question measures the bytes that reach the destination's
network interface per datapoint, for OTLP/gRPC and OTLP/HTTP with and without
gzip, and what each costs in Collector CPU.

## What the documentation says

On the gRPC exporter:

> By default, `gzip` compression is enabled.

Source: [`exporter/otlpexporter/README.md`, line 50](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/otlpexporter/README.md?plain=1#L50).
The HTTP exporter says the same in
[`exporter/otlphttpexporter/README.md`, line 57](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/otlphttpexporter/README.md?plain=1#L57).

The gRPC client settings page, at the same commit:

> If your collector is CPU bound and has a very fast network link, you may benefit from disabling compression, which is the default.

Source: [`config/configgrpc/README.md`, line 89](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/config/configgrpc/README.md?plain=1#L89),
`opentelemetry-collector v0.162.0`, commit
`62cdad2ea133239380b44d20d84eb26e114779b6`, which `otelcol-contrib 0.162.0`
builds on.

All three checked against the raw files at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/otlpexporter/README.md | grep -n 'By default, `gzip` compression is enabled'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/otlphttpexporter/README.md | grep -n 'By default `gzip` compression is enabled'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/config/configgrpc/README.md | grep -n 'disabling compression, which is the default'
```

The two pages at one commit disagree about the default, which is why every
variant here sets `compression` explicitly. Neither page says how many bytes a
datapoint costs, or how much CPU gzip spends to save them.

## Hypothesis

Written before the first run.

- Without compression, gRPC and HTTP deliver within 5% of each other in bytes
  per datapoint, because both carry the same protobuf message, and both land
  between 40 and 80 bytes per datapoint at the sink's interface.
- gzip cuts those bytes by 70% to 90% on both protocols, because the workload
  repeats its metric names and attribute keys.
- gzip raises the Collector's idle-adjusted CPU per datapoint by 10% to 50%
  over the same protocol without it.
- Every variant delivers 100000 datapoints in 20 export requests of 5000, with
  no failed send.

## How to run

```bash
just ask L3-06 4
```

The sender posts 200 OTLP/HTTP JSON requests of 500 datapoints to the Collector:
100000 points of one cumulative sum, `l3_06.requests`. Each point has a
`host.name` from 10 values, a `route` from 5, and a deterministic value and
timestamp. Batching is fixed at `send_batch_size` and `send_batch_max_size`
5000. The sink receives on gRPC 4317 and HTTP 4318 and logs one line per export
request.

| Variant | Exporter | `compression` |
| --- | --- | --- |
| `grpc-none` | `otlp_grpc` to `sink:4317` | `none` |
| `grpc-gzip` | `otlp_grpc` to `sink:4317` | `gzip` |
| `http-none` | `otlp_http` to `http://sink:4318` | `none` |
| `http-gzip` | `otlp_http` to `http://sink:4318` | `gzip` |

Four repetitions instead of three, with the order rotated so each variant runs
once in each position: CPU on a shared Docker Desktop VM drifts during a
session, and a fixed order would hand that drift to one variant.

During the measurement window, Prometheus is stopped so nothing scrapes the
sink. The question then reads three windows from the Docker Engine's cumulative
counters:
1. Idle: Collector CPU nanoseconds and sink RX bytes, until RX stays flat.
2. Workload: the counters across the 200 requests, until the sink log shows
   100000 datapoints and RX stops moving.
3. Quiet: the counters once more.

Prometheus restarts afterwards, and the runner reads the delivery counters.

## What is measured

| Field | Meaning |
| --- | --- |
| sink RX bytes | bytes received on the sink's container interface in the window |
| bytes per datapoint | RX bytes over 100000 |
| Collector CPU ns | cumulative CPU across the window |
| adjusted CPU ns per datapoint | CPU minus the idle rate times the window, over 100000 |
| export requests | summary lines in the sink log, and their item counts |
| delivered | datapoints the sink logged and counted |

A run is invalid when:
- the sink's idle RX rate, applied to the workload window, exceeds 1% of the
  workload RX (see the amendment below);
- a counter is absent or goes backwards, or a container changes;
- an interface other than `eth0` carries bytes;
- the sink does not receive exactly 100000 datapoints;
- an input request is not answered 200, or any send fails or is refused;
- the Collector restarts or is OOM killed;
- Prometheus does not come back.

Amendment, 2026-10-06, after the first session and before the second: the
rule written first required zero sink RX during the idle window. All 16
first-session runs saw 868 to 1130 bytes in 5 idle seconds, with Prometheus
stopped and no workload sent. The traffic did not depend on the variant, and
the first session's results for this question are kept in
[`results/session-1-results.json`](results/session-1-results.json). The rule
now subtracts that idle rate from the workload window and invalidates a run
only when the background exceeds 1% of the workload. The hypothesis is
unchanged. The same session also failed every run on a harness defect: the
order position was checked before it was recorded. A second session ran
before the amended rule reached the code. It failed all 16 runs on the old
idle rule, with 628 to 1130 idle bytes, and is kept in
[`results/session-2-results.json`](results/session-2-results.json).

## Result

16 valid runs in the third session, each variant once in each order position;
the full table is in [`results/REPORT.md`](results/REPORT.md). Medians, with
the range across the four repetitions:

| Variant | Net sink RX bytes | RX bytes per point | Adjusted CPU ns per point |
| --- | --- | --- | --- |
| `grpc-none` | 7338654 (7338136-7339181) | 73.4 | 1802.7 (1720.3-1926.7) |
| `grpc-gzip` | 844364 (844296-844479) | 8.4 | 2150.2 (2072.7-2687.9) |
| `http-none` | 7339793 (7339555-7339913) | 73.4 | 1825.9 (1776.0-1982.5) |
| `http-gzip` | 842562 (842531-842647) | 8.4 | 2245.3 (2088.8-2431.2) |

Every prediction held:
- Without compression the two protocols differed by 1138 bytes in 7.3 MB,
  0.02%, at 73.4 bytes per point, inside 40 to 80.
- gzip cut the bytes by 88.5% on both protocols, inside 70% to 90%.
- gzip raised adjusted CPU per point by 19.3% on gRPC and 23.0% on HTTP,
  inside 10% to 50%. The lowest gzip run of each protocol was above the
  highest uncompressed run.
- Every run delivered 100000 points in 20 requests of 5000, with 0 refused,
  no send-failed counter published, 0 warn lines and 0 error lines.

Background traffic on the sink's `eth0`, scaled to the workload window, was
639 to 934 bytes, under 0.12% of the smallest workload. The sender's 200 JSON
requests were 23458000 bytes. The Docker Engine was 29.8.2, API 1.56.

## What this does not say

The CPU figure is the whole Collector: receiving and decoding 23.5 MB of
OTLP/JSON, batching and exporting. Only the difference between variants is
attributed to compression, about 350 to 420 ns per point. Sink RX is bytes on
one container interface of a Docker Desktop bridge, including TCP, HTTP/2 and
HTTP framing. It is not billable egress: it carries no TLS, crosses no proxy
or load balancer, and is not a cloud provider's metering.

One metric, 50 attribute combinations and deterministic values make the
payload repetitive, which favours gzip; high-entropy attributes compress less,
as `configgrpc` notes. zstd, snappy, other batch sizes, persistent queues and
the receiving side's CPU were not run.
