---
id: L3-05
title: What does deriving RED metrics from spans cost in series?
level: 3
theme: traces, metrics
status: measured
---

## Question

The `spanmetrics` connector turns spans into request, error and duration
metrics without instrumenting anything twice. Its cost is a series count that
grows with every dimension configured on it. This question measures the series
one bounded and one unique-per-request dimension produce. It also measures how
accurate the derived request count, error rate and duration stay after 10% head
sampling or an error-only tail policy.

## What the documentation says

> - `dimensions`: the list of dimensions to add to `traces.span.metrics.calls`, `traces.span.metrics.duration` and `traces.span.metrics.event` metrics with the default dimensions defined above.

> - `aggregation_cardinality_limit` (default: `0`): Defines the maximum number of unique combinations of dimensions that will be tracked for metrics aggregation.

Source: [`connector/spanmetricsconnector/README.md`, lines 125 and 155](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/connector/spanmetricsconnector/README.md?plain=1#L125),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`. Lines 60-66 list the default
dimensions: `service.name`, `span.name`, `span.kind`, `status.code` and
`collector.instance.id`.

Checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/connector/spanmetricsconnector/README.md | grep -n 'the list of dimensions to add to\|Defines the maximum number of unique combinations'
```

With the limit at its default of `0`, nothing bounds the combinations. The page
does not say how many series one combination costs once the duration histogram
is exported, or what sampling upstream of the connector does to its numbers.

## Hypothesis

Written before the first run.

- Every combination of dimensions costs 10 Prometheus series: 1 for calls, and
  9 for the duration histogram (7 buckets including `+Inf`, plus sum and count).
- `bounded-all`: 10 combinations (5 routes x 2 status codes), 100 series. Calls
  read 1000, errors 100, mean duration 190 ms.
- `unbounded-all`: 1000 combinations, 10000 series, the same correct calls,
  errors and mean.
- `unbounded-head-10`: 70 to 130 combinations and 10 times that in series.
  Calls scaled by 10 land within 30% of 1000, and the error rate between 3% and
  20%.
- `unbounded-tail-errors`: exactly 100 combinations and 1000 series. Calls read
  100, the error rate 100%, and the mean duration 1000 ms.
- Sampling therefore cuts the series of an unbounded dimension in proportion to
  what it keeps. The error-only policy costs as many series as 10% head
  sampling and describes none of the successful traffic.

## How to run

```bash
just ask L3-05
```

A question-local emitter runs the Python SDK `1.45.0` with deterministic ids:
1000 traces of one `SERVER` span named after its route. Five routes, 200 spans
each, 20 errors per route. Each span has `http.route` and a unique
`customer.id`. Successes last 100 ms and errors 1000 ms, set through explicit
start and end times.

| Variant | SDK sampler | Collector | `spanmetrics` dimensions |
| --- | --- | --- | --- |
| `bounded-all` | `ParentBased(ALWAYS_ON)` | no sampling | `http.route` |
| `unbounded-all` | `ParentBased(ALWAYS_ON)` | no sampling | `http.route`, `customer.id` |
| `unbounded-head-10` | `ParentBased(TraceIdRatioBased(0.10))` | no sampling | `http.route`, `customer.id` |
| `unbounded-tail-errors` | `ParentBased(ALWAYS_ON)` | `tail_sampling` `status_code: ERROR` | `http.route`, `customer.id` |

The connector sits after `tail_sampling`, so it sees only retained spans. It
flushes every second with explicit buckets `50ms, 100ms, 250ms, 500ms, 1s,
2s`, and its metrics leave on the `prometheus` exporter.

## What is measured

| Field | Meaning |
| --- | --- |
| calls, bucket, sum, count series | series per `l3_05` family on the `exported` job |
| combinations | series of `l3_05_calls_total`, one per dimension set |
| customers | distinct `customer_id` label values |
| requests, errors | sums of the calls counter, all and `STATUS_CODE_ERROR` |
| error rate, mean, p95 | from calls, the duration sum and count, and `histogram_quantile` |
| scaled requests | requests times 10 for the head sampler |

A run is invalid when:
- the emitter does not produce exactly 1000 traces, 200 per route and 20 errors
  per route;
- the sink receives an unknown trace, or a span count that differs from what
  the sampler kept;
- the calls or histogram count differ from the retained spans, or the errors
  from the retained error spans;
- a family is missing or the readings do not settle;
- `tail_sampling` does not decide all 1000 traces or drops one early;
- the Collector restarts or is OOM killed.

A series count other than the predicted one is the result, not a failure.

## Result

12 valid runs, every repetition identical in its counts; the full table is in
[`results/REPORT.md`](results/REPORT.md).

| Variant | Combinations | Series | Requests (scaled) | Error rate % | Mean ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| `bounded-all` | 10 | 100 | 1000 | 10.0 | 190.0 | 750.0 |
| `unbounded-all` | 1000 | 10000 | 1000 | 10.0 | 190.0 | 750.0 |
| `unbounded-head-10` | 103 | 1030 | 1030 | 5.8 | 152.4 | 570.8 |
| `unbounded-tail-errors` | 100 | 1000 | 100 | 100.0 | 1000.0 | 975.0 |

Every prediction held: 10 series per combination, the series counts of the
complete variants, 103 head combinations inside 70 to 130, scaled calls 3.0%
high inside 30%, an error rate of 5.8% inside 3% to 20%, and the tail variant's
100 calls at 100% errors and 1000 ms.

Two results were not predicted. `histogram_quantile` returned a p95 of 750 ms
against a true 1000 ms with nothing sampled, because it interpolates inside the
`500ms` to `1s` bucket. Peak Collector heap rose from 30.6-33.6 MiB to
49.9-57.8 MiB with the unbounded dimension.

## What this does not say

One service, single-span traces, a 1000-request burst with fixed ids, one
bucket layout and a fresh Prometheus. An id seen once is the worst case: a
returning customer reuses its series, and churn, expiration and retention were
not measured. `aggregation_cardinality_limit`, exponential histograms and
deriving metrics before sampling were not run.
