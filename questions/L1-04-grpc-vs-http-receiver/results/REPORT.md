# L1-04 What does choosing gRPC over HTTP on the receiver cost or save?

Level 1, theme collector. 5 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L1-04`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `grpc-none` | protobuf over gRPC, no compression |
| `grpc-gzip` | protobuf over gRPC, gzip |
| `http-proto-none` | HTTP protobuf, no compression |
| `http-proto-gzip` | HTTP protobuf, gzip |
| `http-json-none` | HTTP JSON, no compression |

## Measured

| Variant | Points sent | Sent bytes | Collector RX bytes | Background RX bytes | Net RX bytes | RX bytes per point | Collector CPU ns per point | Sender CPU ns per point | Points per second | Failed requests | Export requests | Delivered | Collector accepted | Sink accepted | JSON on the gRPC port | Order position | Warn lines | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `grpc-none` | 100000 | 7319000 | 7404251 (7403921 to 7404656) | 183 (59 to 241) | 7404038 (7403680 to 7404473) | 74.0 | 822.2 (789.0 to 1050.2) | 526.9 (516.0 to 625.8) | 710363 (629376 to 752964) | 0 | 20 | 100000 | 100000 | 100000 | BadStatusLine | 3 (1 to 5) | 0 | 0 |
| `grpc-gzip` | 100000 | 7319000 | 892086 (891228 to 893947) | 123 (59 to 215) | 891987 (891105 to 893887) | 8.9 | 1131.7 (1070.9 to 1146.4) | 957.2 (873.5 to 1030.3) | 533919 (476173 to 590596) | 0 | 20 | 100000 | 100000 | 100000 | BadStatusLine | 3 (1 to 5) | 0 | 0 |
| `http-proto-none` | 100000 | 7319000 | 7387296 (7387230 to 7392840) | 161 (91 to 271) | 7387135 (7386959 to 7392749) | 73.9 | 836.5 (814.4 to 935.5) | 221.9 (205.6 to 245.2) | 828985 (759047 to 852132) | 0 | 20 | 100000 | 100000 | 100000 | BadStatusLine | 3 (1 to 5) | 0 | 0 |
| `http-proto-gzip` | 100000 | 800090 | 859654 (859324 to 860149) | 327 (125 to 407) | 859529 (858917 to 859775) | 8.6 | 1104.0 (887.5 to 1138.6) | 1612.6 (1393.6 to 1672.0) | 385238 (373842 to 440790) | 0 | 20 | 100000 | 100000 | 100000 | BadStatusLine | 3 (1 to 5) | 0 | 0 |
| `http-json-none` | 100000 | 21753600 | 21847758 (21847534 to 21848550) | 221 (62 to 252) | 21847536 (21847339 to 21848488) | 218.5 | 1586.7 (1469.5 to 1682.7) | 1306.9 (1239.0 to 1342.2) | 329092 (314338 to 338539) | 0 | 20 | 100000 | 100000 | 100000 | BadStatusLine | 3 (1 to 5) | 0 | 0 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The protocol barely changes the bytes, the encoding and compression do. For
100000 points, uncompressed gRPC and HTTP protobuf reached the Collector as
74.0 and 73.9 bytes per point, 0.23% apart. gzip took both to under 9 bytes
per point, 88% less, and HTTP JSON cost 218.5, 2.96 times protobuf.

The Collector paid for what it had to undo. JSON cost it 1586.7 ns per point
against 836.5 for HTTP protobuf, 1.90 times, and gzip added 32% to 38%. The
sender paid more: gzip raised its CPU per point by 82% over gRPC and by 627%
over HTTP, and on loopback cut throughput by 25% and 54%. Uncompressed HTTP
protobuf was the fastest and the cheapest for this sender.

The README's "the HTTP/JSON address is the same as gRPC" did not hold: all 25
JSON requests to the gRPC port failed with `BadStatusLine`.

What this does to a bill: on a billed link, gzip divides receiver ingress by
about 8.5 for a third more Collector CPU. JSON triples the bytes and doubles
the Collector's CPU, so it belongs where a browser or a script cannot speak
protobuf, not on a service's hot path.

## What this does not say

One Python sender over loopback, in sequence, so sender CPU and throughput
describe `grpcio` and Python's `gzip`, not an SDK exporter or a real network.
Collector RX is one container interface behind Docker Desktop's port forwarding,
not billable ingress, with no TLS, proxy or load balancer. zstd, snappy,
concurrent senders and gzipped JSON were not run.
