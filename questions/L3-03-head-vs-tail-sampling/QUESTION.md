---
id: L3-03
title: How many traces with errors survive head sampling and tail sampling?
level: 3
theme: traces
status: measured
---

## Question

Head sampling decides before the trace exists, tail sampling decides after. For
the same traffic with the same error rate, this question measures how many error
traces each keeps, how much it stores, what it costs the Collector in memory,
and what it does to the request count and error rate a dashboard derives from
the spans it receives.

## What the documentation says

On head sampling in the SDK:

> * The sampling algorithm MUST be deterministic. A trace identified by a given
>   `TraceId` is sampled or not independent of language, time, etc.

Source: [`specification/trace/sdk.md`, lines 461-463](https://github.com/open-telemetry/opentelemetry-specification/blob/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/trace/sdk.md?plain=1#L461-L463),
OpenTelemetry specification `v1.61.0`, commit
`32c0b2c1d9e11f68da0e6e7b63044e57878590de`. The same section, lines 436-444,
deprecates `TraceIdRatioBased` in favor of the composable `ProbabilitySampler`
and forbids SDKs to change its behavior before January 1, 2027. The Python SDK
`1.45.0` samples when the low 64 bits of the trace id fall under
`round(ratio * 2**64)`
([`sampling.py`, lines 262-287](https://github.com/open-telemetry/opentelemetry-python/blob/4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2/opentelemetry-sdk/src/opentelemetry/sdk/trace/sampling.py#L262-L287),
commit `4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2`).

On tail sampling in the Collector:

> The processor keeps spans in memory while it waits to make a sampling decision.

> - When there's a "sample" decision, the trace is sampled;

> A trace is dropped without sampling if it's removed from the circular buffer before `decision_wait`.

Source: [`processor/tailsamplingprocessor/README.md`, lines 25, 108 and 620](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/tailsamplingprocessor/README.md?plain=1#L25),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`.

All quotes checked against the raw files at those commits:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/trace/sdk.md | grep -n 'The sampling algorithm MUST be deterministic'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/tailsamplingprocessor/README.md | grep -n 'keeps spans in memory while\|"sample" decision, the trace is sampled\|dropped without sampling if'
```

The pages say how each sampler decides. They do not say how many error traces a
10% head sampler keeps against a tail policy, what the stored volume is, or how
far a dashboard that counts received spans moves away from the real traffic.

## Hypothesis

Written before the first run.

- `all`: 1000 traces and 5000 spans delivered, 100 of them error traces, every
  trace complete, observed error rate 10%.
- `head-10`: between 70 and 130 traces delivered, every one complete, and about
  a tenth of the 100 error traces, between 3 and 20. The observed error rate
  stays near 10%, but the request count a dashboard derives is about a tenth of
  the real one. The Collector accepts only the selected spans, so its peak heap
  is no higher than `all`.
- `tail-probabilistic-10`: the same shape as `head-10` in what is delivered, a
  different set of traces because the processor hashes with its own salt, and a
  Collector that accepts all 5000 spans and holds them for `decision_wait`.
- `tail-errors`: exactly the 100 error traces and their 500 spans, an error
  capture of 100%, a stored volume of 10%, and a dashboard error rate of 100%.
- `tail-errors-plus-10`: the 100 error traces plus between 60 and 120 of the 900
  successful ones, and a dashboard error rate between 45% and 63%.
- Every tail variant accounts for all 1000 traces in its decisions, drops none
  early, raises the peak heap above `all` by less than 50 MiB, stays under the
  512m ceiling, and takes at least the 5 second `decision_wait` longer to drain.

## How to run

```bash
just ask L3-03
```

A question-local emitter runs the Python SDK `1.45.0` with a deterministic id
generator, so every repetition produces the same 1000 trace ids. Each trace has
a root and four children. Every tenth trace, 100 in all, sets `ERROR` on its
third child. The SDK exports with `BatchSpanProcessor` over OTLP/HTTP to a
Collector with a 512m container ceiling and
`memory_limiter -> [tail_sampling] -> batch`, which exports to a sink printing
every span.

| Variant | SDK sampler | Collector |
| --- | --- | --- |
| `all` | `ParentBased(ALWAYS_ON)` | no sampling |
| `head-10` | `ParentBased(TraceIdRatioBased(0.10))` | no sampling |
| `tail-probabilistic-10` | `ParentBased(ALWAYS_ON)` | `probabilistic` 10% |
| `tail-errors` | `ParentBased(ALWAYS_ON)` | `status_code` `ERROR` |
| `tail-errors-plus-10` | `ParentBased(ALWAYS_ON)` | both policies |

`tail_sampling` runs with `decision_wait: 5s` and `num_traces: 5000`, five
times the workload. Configs live in `config/`.

## What is measured

| Field | Meaning |
| --- | --- |
| generated, accepted, delivered | spans and traces at the SDK, the Collector receiver and the sink |
| complete, incomplete | delivered traces with all 5 spans, and with fewer |
| error capture | delivered error traces over the 100 generated |
| retained, relative storage | delivered spans over the 5000 generated |
| request bias | delivered traces over the 1000 generated, what an unadjusted count shows |
| observed error rate | delivered error traces over delivered traces, against 10% |
| heap, RSS | peak `otelcol_process_runtime_heap_alloc_bytes` and `otelcol_process_memory_rss` |
| decisions, early drops | `tail_sampling` traces decided and `sampling_trace_dropped_too_early` |
| drain | seconds from the emitter's flush to the last change at the sink |

A run is invalid when the emitter fails, a delivered trace was never generated
or is incomplete, the sink counter disagrees with the sink log, the tail
decisions do not add up to 1000, any trace is dropped early, or the Collector
restarts or is OOM killed.

## Result

15 valid runs; the full table is in [`results/REPORT.md`](results/REPORT.md).
Medians, and every repetition agreed on the counts:

| Variant | Delivered traces | Error traces kept | Retained % | Observed error rate % | Heap peak MiB |
| --- | --- | --- | --- | --- | --- |
| `all` | 1000 | 100 | 100.0 | 10.0 | 31.7 |
| `head-10` | 98 | 12 | 9.8 | 12.2 | 29.3 |
| `tail-probabilistic-10` | 114 | 14 | 11.4 | 12.3 | 33.9 |
| `tail-errors` | 100 | 100 | 10.0 | 100.0 | 33.4 |
| `tail-errors-plus-10` | 200 | 100 | 20.0 | 50.0 | 32.2 |

Head sampling kept 12 of 100 error traces; `status_code: ERROR` kept all 100
at the same 10% of stored volume. Every delivered trace was complete,
`tail_sampling` decided all 1000 traces with no early drop, and no Collector
restarted or neared its 512m ceiling.

Against the hypothesis: the trace and error counts fell inside every predicted
range, and the heap rose by less than 5 MiB, inside the 50 MiB predicted. The
drain prediction failed. `tail_sampling` finished 6.0 to 7.1 seconds after the
flush, about one second after the variants without it, not five.

The first full run was invalid and is not reported: the SDK's default
`max_queue_size` of 2048 dropped about half of the 5000-span burst, so the
Collector accepted 2560 spans and the tail decisions never reached 1000. The
emitter now uses a queue of 8192. A second run was valid by the original rules
but recorded no heap, RSS or drain for the variants without `tail_sampling`;
a missing peak or drain now invalidates a run, and the third run recorded all
three in every repetition.

## What this does not say

One service, one Collector, a 1000-trace burst with fixed ids, so 98 and 114
are one draw of each hash, not a distribution. Traces spread across services,
Collectors or more than `decision_wait`, late spans and the decision cache are
not tested. Memory is a one second scrape of small spans. Storage is relative
volume; no vendor price is applied. The drain floor of about 5 seconds common to
every variant was not isolated.
