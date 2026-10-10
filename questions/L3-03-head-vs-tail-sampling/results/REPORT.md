# L3-03 How many traces with errors survive head sampling and tail sampling?

Level 3, theme traces. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L3-03`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `all` | SDK keeps everything, no Collector sampling |
| `head-10` | SDK ParentBased(TraceIdRatioBased(0.10)), no Collector sampling |
| `tail-probabilistic-10` | tail_sampling probabilistic 10% |
| `tail-errors` | tail_sampling status_code ERROR |
| `tail-errors-plus-10` | tail_sampling status_code ERROR or probabilistic 10% |

## Measured

| Variant | Traces generated | Spans generated | Error traces generated | SDK selected | Collector accepted spans | Delivered traces | Delivered spans | Complete | Incomplete | Error traces delivered | Success traces delivered | Error capture % | Retained % | Request ratio | Observed error rate % | Heap peak MiB | RSS peak MiB | Tail decisions | Tail sampled | Dropped too early | Drain s | OOM killed | Restarts | SDK exceptions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `all` | 1000 | 5000 | 100 | 1000 | 5000 | 1000 | 5000 | 1000 | 0 | 100 | 900 | 100.0 | 100.0 | 1.000 | 10.0 | 31.7 (30.7 to 32.4) | 203.9 (202.2 to 205.5) | - | - | - | 5.0 (5.0 to 6.0) | False | 0 | 0 |
| `head-10` | 1000 | 5000 | 100 | 98 | 490 | 98 | 490 | 98 | 0 | 12 | 86 | 12.0 | 9.8 | 0.098 | 12.2 | 29.3 (29.0 to 30.6) | 204.1 (200.9 to 205.1) | - | - | - | 5.0 (5.0 to 6.0) | False | 0 | 0 |
| `tail-probabilistic-10` | 1000 | 5000 | 100 | 1000 | 5000 | 114 | 570 | 114 | 0 | 14 | 100 | 14.0 | 11.4 | 0.114 | 12.3 | 33.9 (33.4 to 36.1) | 206.5 (206.3 to 208.5) | 1000 | 114 | 0 | 6.0 (6.0 to 7.1) | False | 0 | 0 |
| `tail-errors` | 1000 | 5000 | 100 | 1000 | 5000 | 100 | 500 | 100 | 0 | 100 | 0 | 100.0 | 10.0 | 0.100 | 100.0 | 33.4 (32.6 to 36.3) | 206.6 (205.5 to 208.3) | 1000 | 100 | 0 | 7.0 (6.0 to 7.1) | False | 0 | 0 |
| `tail-errors-plus-10` | 1000 | 5000 | 100 | 1000 | 5000 | 200 | 1000 | 200 | 0 | 100 | 100 | 100.0 | 20.0 | 0.200 | 50.0 | 32.2 (32.2 to 35.1) | 206.5 (204.6 to 206.8) | 1000 | 200 | 0 | 7.0 (6.0 to 7.0) | False | 0 | 0 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Head sampling kept 12 of the 100 error traces. `ParentBased(TraceIdRatioBased
(0.10))` selected the same 98 of 1000 traces in all three repetitions, because
the ids are deterministic and so is the sampler, and 12 of those 98 carried the
error. 88 errors were never exported, and nothing downstream can recover them.

Tail sampling on status kept all 100. `status_code: ERROR` delivered exactly the
100 error traces and their 500 spans, 10.0% of the volume, with every trace
complete. The dashboard built on what it stored reads an error rate of 100% and
a request count of 100, against 10% and 1000 in the traffic.

No variant keeps a dashboard honest without correction. `head-10` and
`tail-probabilistic-10` kept 98 and 114 traces, request ratios of 0.098 and
0.114, and observed error rates of 12.2% and 12.3% against the real 10.0%. The
combined policy kept the 100 errors plus 100 of the 900 successes, 1000 spans,
20.0% of the volume, and an observed error rate of 50.0%. An unadjusted count
of received spans measures the sampler, not the traffic.

The Collector paid little for 1000 buffered traces. Peak heap on the one second
scrape was 30.7 to 32.4 MiB under `all` and 32.2 to 36.3 MiB with
`tail_sampling`, peak RSS 202.2 to 208.5 MiB, under the 512m ceiling in every
run. `tail_sampling` decided all 1000 traces in every tail run and dropped none
early.

The drain did not separate the samplers as the author predicted. The last
change at the sink came 5.0 to 6.0 seconds after the flush without
`tail_sampling` and
6.0 to 7.1 seconds with `tail_sampling`, not the 5 seconds longer that
`decision_wait: 5s` suggested. Every variant shows a floor of about 5 seconds
that this question did not isolate.

What this does to a bill: the error-only policy stores 10% of the volume and
keeps every failure; the 10% samplers store the same 10% and lose 86 to 88% of
them. A request count or error rate on sampled spans needs the sampling ratio
applied, and an error-keeping policy has no single ratio to apply.

## What this does not say

Measured with `opentelemetry-sdk 1.45.0`, one service, one Collector and
`otelcol-contrib 0.162.0`, 1000 traces of 5 spans emitted in a burst under
0.05 seconds, with the error on the third child of every tenth trace. The trace
ids are fixed, so the 98 and 114 selected traces are one draw of each hash, not
a distribution; other ids would select other counts around 100.

Traces whose spans come from several services, arrive over more than
`decision_wait`, or are spread across several Collectors are not tested. Late
spans, the decision cache and `span-ingest` are not exercised. Memory comes
from a one second scrape and 5000 small spans; it says nothing about
`num_traces` at production rates. The SDK queue was raised to 8192 because the
default of 2048 dropped half of the burst before export; at the default, the
SDK loses spans before any sampler sees them. No vendor price is applied, so
storage is relative volume, not cost.
