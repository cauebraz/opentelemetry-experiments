# L3-06 How many bytes does a datapoint cost on the wire?

Level 3, theme collector. 4 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L3-06`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `grpc-none` | OTLP/gRPC to the sink, compression none |
| `grpc-gzip` | OTLP/gRPC to the sink, compression gzip |
| `http-none` | OTLP/HTTP protobuf to the sink, compression none |
| `http-gzip` | OTLP/HTTP protobuf to the sink, compression gzip |

## Measured

| Variant | Points sent | Input request bytes | Sink RX bytes | RX bytes per point | Collector CPU ns | Adjusted CPU ns per point | Idle sink RX bytes | Background RX bytes | Net RX bytes | Work seconds | Export requests | Delivered | Collector accepted | Sink accepted | Send failed | Order position | Warn lines | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `grpc-none` | 100000 | 23458000 | 7339518 (7339004 to 7339820) | 73.4 | 180394500 (172185000 to 192764000) | 1802.7 (1720.3 to 1926.7) | 1088 (800 to 1088) | 864 (639 to 868) | 7338654 (7338136 to 7339181) | 3.99 (3.96 to 3.99) | 20 | 100000 | 100000 | 100000 | - | 2 (1 to 4) | 0 | 0 |
| `grpc-gzip` | 100000 | 23458000 | 845223 (845157 to 845282) | 8.4 | 215265500 (207356000 to 268795000) | 2150.2 (2072.7 to 2687.9) | 1109 (848 to 1130) | 884 (678 to 934) | 844364 (844296 to 844479) | 4.00 (3.98 to 4.13) | 20 | 100000 | 100000 | 100000 | - | 2 (1 to 4) | 0 | 0 |
| `http-none` | 100000 | 23458000 | 7340533 (7340434 to 7340632) | 73.4 | 182589000 (177597000 to 198245000) | 1825.9 (1776.0 to 1982.5) | 957 (826 to 1088) | 772 (653 to 881) | 7339793 (7339555 to 7339913) | 4.03 (3.95 to 4.05) | 20 | 100000 | 100000 | 100000 | - | 2 (1 to 4) | 0 | 0 |
| `http-gzip` | 100000 | 23458000 | 843413 | 8.4 | 224530500 (208883000 to 243121000) | 2245.3 (2088.8 to 2431.2) | 1067 (938 to 1088) | 852 (766 to 882) | 842562 (842531 to 842647) | 4.03 (3.97 to 4.08) | 20 | 100000 | 100000 | 100000 | - | 2 (1 to 4) | 0 | 0 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Compression decides the bytes, and the protocol does not. Without compression,
OTLP/gRPC and OTLP/HTTP protobuf delivered 100000 points as 7338654 and 7339793
bytes at the sink's interface, 73.4 bytes per point, 0.02% apart. gzip took
both to 8.4 bytes per point, 88.5% less.

The bytes saved cost CPU. Idle-adjusted Collector CPU rose from 1802.7 to
2150.2 ns per point on gRPC, 19.3%, and from 1825.9 to 2245.3 on HTTP, 23.0%.
Every gzip run sat above every uncompressed run of its protocol, across four
balanced orders.

What this does to a bill: on a link where bytes are billed, gzip divides the
telemetry by about 8.7 for roughly a fifth more Collector CPU. Two pages at the
same commit disagree about the default, so the setting belongs in the config.

## What this does not say

The CPU figure covers the whole Collector, including decoding the sender's
23.5 MB of OTLP/JSON; only the differences are compression. Sink RX is one
container interface on a Docker Desktop bridge, with TCP and HTTP framing. It
is not billable egress, and there is no TLS, proxy or load balancer. The
payload is one repetitive metric, which favours gzip; zstd, snappy, other batch
sizes and the receiver's CPU were not run.
