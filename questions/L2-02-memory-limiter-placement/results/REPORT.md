# L2-02 Does memory_limiter belong before or after batch?

Level 2, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L2-02`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `control` | memory_limiter first, 20 requests, the heap stays under the soft limit |
| `limiter-first` | memory_limiter then batch, up to 200 requests with the sink stopped |
| `batch-first` | batch then memory_limiter, up to 200 requests with the sink stopped |

## Measured

| Variant | Requests sent | Records sent | Statuses | First refused request | Accepted | Receiver refused | Limiter refused | Heap peak MiB | RSS peak MiB | Queue size max | Latency p50 ms | Latency max ms | Sink accepted | Delivered | Duplicates | Acked, not delivered | Refused records | Refusing lines | Sender failed lines | Seconds to drain | OOM killed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `control` | 20 | 200 | 200:20 | - | 200 | 0 | - | 38.4 (37.9 to 39.1) | 226.6 (226.4 to 230.0) | 7 | 8.6 (7.4 to 8.8) | 17.5 (13.2 to 17.7) | 200 | 200 | 0 | 0 | 0 | 0 | 0 | 11.2 (8.2 to 11.2) | False |
| `limiter-first` | 57 (56 to 58) | 570 (560 to 580) | varies: 200:57; 503:1, 200:55; 503:1, 200:56; 503:1 | 56 (55 to 57) | 560 (550 to 570) | 10 | 10 | 89.8 (84.9 to 90.2) | 266.0 (259.4 to 267.2) | 18 | 8.8 (7.9 to 9.0) | 16.4 (16.0 to 17.9) | 560 (550 to 570) | 560 (550 to 570) | 0 | 0 | 10 | 2 (1 to 2) | 0 | 19.3 (12.2 to 20.3) | False |
| `batch-first` | 200 | 2000 | 200:200 | - | 2000 | 0 | 1140 (1130 to 1140) | 108.1 (104.9 to 111.9) | 290.6 (290.1 to 293.3) | 27 | 8.6 (8.1 to 8.9) | 18.3 (17.3 to 18.9) | 860 (860 to 870) | 860 (860 to 870) | 0 | 1140 (1130 to 1140) | 0 | 3 (2 to 4) | 10 | 22.4 (14.3 to 22.4) | False |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

With batch in front, 1130 to 1140 records were answered with HTTP 200 and never
arrived. All 200 requests got 200, the receiver refused 0 records, and the sink
received 860 to 870 of the 2000 sent. The limiter refused the batch processor,
the batch processor logged `Sender failed` with
`"error": "data refused due to high memory usage"` at `warn`, and nothing
retried. The sender was told every record was accepted.

With the limiter first, nothing acknowledged was lost. The heap crossed the soft
limit after 55 to 57 requests, the next request got HTTP 503, and the receiver
counted those 10 records as refused. Every record of every request answered 200
reached the sink after recovery, in all three repetitions. The loss is exactly
the refused request, and the sender holds a status code that says so.

The one counter that sees the loss in both orders is
`otelcol_processor_memory_limiter_refused_log_records`. Under `batch-first` it
equals the acknowledged records that never arrived, 1140, 1130 and 1140, run
for run. Under `limiter-first` it reads 10, the refused request. The metric does
not exist in the control, where the limiter never refused.

The `Sender failed` line carries no item count and appeared exactly 10 times in
each `batch-first` run, the Collector log sampler's first-ten allowance. It
says that a loss happened, not how large it was.

The peak heap failed the author's prediction. `batch-first` peaked at 104.9 to
111.9 MiB on the one second scrape, above the 96 MiB hard limit, while
`limiter-first` peaked at 84.9 to 90.2 MiB. The batch processor accepts and
holds data before the limiter can refuse it, so the limiter acts later and the
heap goes further. RSS reached 290.1 to 293.3 MiB, under the 512m container
ceiling, and no run was OOM killed.

Latency did not separate the orders: median round trip 7.4 to 9.0 ms in every
variant. A 503 is the only thing the sender can see.

What this does to a pipeline: put `memory_limiter` first, and alert on
`otelcol_processor_memory_limiter_refused_*` as data loss whenever any
processor sits in front of the limiter. Behind batch, that counter is the only
number that measures what was acknowledged and dropped.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with logs only, one OTLP/HTTP receiver,
`memory_limiter` at `limit_mib: 96` and `spike_limit_mib: 24`, the batch
processor at its defaults, and a sink stopped for the whole sending phase.
Requests of 10 records with a 64 KiB attribute each, 50 ms apart. The pressure
comes from the exporter queue holding undeliverable data; a burst of large
requests against a healthy destination is a different shape of pressure.

The sender here stops at the first non-200 status and does not retry. A client
that retries a 503, as the OTLP specification asks, would turn the 10 refused
records of `limiter-first` into delayed delivery, but that client was not run.

The OTLP/gRPC receiver, traces and metrics, other processors in front of the
limiter, `memory_limiter` as an extension on the receiver, and a persistent
queue are not tested. Heap and RSS peaks come from a one second scrape and can
miss a shorter peak. Log line counts are floors under the Collector's default
sampler.
