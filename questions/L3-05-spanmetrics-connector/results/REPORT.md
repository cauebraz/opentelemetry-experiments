# L3-05 What does deriving RED metrics from spans cost in series?

Level 3, theme traces, metrics. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L3-05`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `bounded-all` | dimension http.route, every span kept |
| `unbounded-all` | dimensions http.route and customer.id, every span kept |
| `unbounded-head-10` | unbounded dimensions after SDK TraceIdRatioBased(0.10) |
| `unbounded-tail-errors` | unbounded dimensions after tail_sampling status_code ERROR |

## Measured

| Variant | Traces generated | SDK selected | Retained spans | Combinations | Calls series | Bucket series | Sum series | Count series | Total series | Series per combination | Series per request | Customers | Requests | Requests scaled | Request error % | Errors | Error capture % | Error rate % | Mean ms | Mean error % | p95 ms | Tail decisions | Dropped too early | Heap peak MiB | Settle s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bounded-all` | 1000 | 1000 | 1000 | 10 | 10 | 70 | 10 | 10 | 100 | 10.0 | 0.10 | 0 | 1000 | 1000 | 0.0 | 100 | 100.0 | 10.0 | 190.0 | 0.0 | 750.0 | - | - | 32.7 (30.6 to 33.6) | 10.1 (10.1 to 11.1) |
| `unbounded-all` | 1000 | 1000 | 1000 | 1000 | 1000 | 7000 | 1000 | 1000 | 10000 | 10.0 | 10.00 | 1000 | 1000 | 1000 | 0.0 | 100 | 100.0 | 10.0 | 190.0 | 0.0 | 750.0 | - | - | 50.1 (49.9 to 57.8) | 11.1 |
| `unbounded-head-10` | 1000 | 103 | 103 | 103 | 103 | 721 | 103 | 103 | 1030 | 10.0 | 1.03 | 103 | 103 | 1030 | 3.0 | 6 | 6.0 | 5.8 | 152.4 | -19.8 | 570.8 | - | - | 38.6 (35.1 to 39.1) | 11.1 |
| `unbounded-tail-errors` | 1000 | 1000 | 100 | 100 | 100 | 700 | 100 | 100 | 1000 | 10.0 | 1.00 | 100 | 100 | 100 | -90.0 | 100 | 100.0 | 100.0 | 1000.0 | 426.3 | 975.0 | 1000 | 0 | 40.0 (38.6 to 41.2) | 14.1 (13.1 to 14.1) |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

One combination of dimensions cost exactly 10 series in every run: 1 calls
series and 9 for the duration histogram, 7 buckets plus sum and count. The
bounded route dimension made 10 combinations and 100 series. Adding a
unique-per-request `customer.id` made 1000 combinations and 10000 series, 10
series per request, while the requests, the 100 errors and the 190.0 ms mean
stayed exact.

The unbounded dimension also cost the Collector memory. Peak heap on the one
second scrape was 30.6 to 33.6 MiB with the route alone and 49.9 to 57.8 MiB
with `customer.id`, for 1000 spans.

Head sampling cut the series in proportion and bent every RED number. The SDK
kept 103 traces, which made 103 combinations and 1030 series. Scaled by 10,
calls read 1030, 3.0% high. The errors kept were 6 of 100, so the error rate
read 5.8% against 10.0%, and the mean duration read 152.4 ms, 19.8% low.

The error-only tail policy cost 1000 series, the same order as the head
sampler, and described none of the successful traffic: 100 calls, an error rate
of 100%, and a mean of 1000.0 ms.

The histogram misread the percentile with no sampling at all. The true p95 is
1000 ms, since 10% of requests take 1000 ms. `histogram_quantile` interpolates
inside the `500ms` to `1s` bucket and returned 750.0 ms whenever every span was
kept. It returned 570.8 ms after head sampling and 975.0 ms after the error-only
policy.

What this does to a bill: a span attribute promoted to a `spanmetrics`
dimension costs 10 series per distinct value, so a per-request value turns 1000
requests into 10000 series. Sampling upstream lowers that bill by changing what
the metrics say. Derive RED metrics before sampling, on bounded dimensions only.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one service, single-span traces,
1000 requests in one burst, explicit buckets of `50ms, 100ms, 250ms, 500ms, 1s,
2s`, cumulative temporality and a fresh Prometheus. A customer that returns
reuses its series. The count here is the worst case of an id seen once, and it
says nothing about churn, `metrics_expiration` or retention.

`aggregation_cardinality_limit`, exponential histograms, `exemplars` and
deriving metrics before sampling were not run. The head sample is one draw of
fixed ids, not a distribution. The heap peaks come from a one second scrape
and 1000 small spans.
