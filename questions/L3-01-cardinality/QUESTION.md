---
id: L3-01
title: What does one unbounded attribute cost, and what actually removes the cost?
level: 3
theme: metrics
status: measured
evidence_repository: https://github.com/cauebraz/opentelemetry-cardinality-lab
evidence_commit: ad5779d31d33c876f84d5e2bf164d427b89b356e
evidence_results: https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/benchmark/results.json
---

## Question

One label that takes a new value per request, such as a user id or a raw URL
path, turns one metric into as many series as there are values. The question is
how many series that is against a Collector that does nothing, and which of the
Cardinality Guardian's three modes brings the number back down.

## What the documentation says

On the mode that only marks the problem:

> **Note:** `tag_only` does **not** protect your TSDB on its own

On the metric types the enforcing modes cannot reduce:

> **Note:** For metric types that don't yet support reaggregation, the processor automatically falls back to `tag_only` behavior with an `otel.metric.overflow` tag, ensuring no data corruption.

Source: [`processor/cardinalityguardianprocessor/README.md`, lines 125 and 154](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/cardinalityguardianprocessor/README.md?plain=1#L125-L154),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`.

Both quotes checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/cardinalityguardianprocessor/README.md | grep -n 'does \*\*not\*\* protect\|automatically falls back'
```

The page says which types fall back. It does not say how many series reach the
backend in each mode, how that scales with the number of new values, or what a
burst does against a value churn spread over epochs.

## Hypothesis

This question was measured in the external lab before this repository existed,
and no prediction was written here before that run. A hypothesis written now,
with the numbers in view, would not be one, so none is recorded. The result
below reads the lab's evidence and nothing else.

## How to run

The run lives in the lab, at the commit pinned in the front matter:

```bash
git clone https://github.com/cauebraz/opentelemetry-cardinality-lab
cd opentelemetry-cardinality-lab
git checkout ad5779d31d33c876f84d5e2bf164d427b89b356e
just setup
just benchmark
```

84 runs, three repetitions per case, a threshold of 20 new values per 10 second
epoch, on `otelcol-contrib 0.162.0` and Prometheus `3.15.0`. The lab's
`results/benchmark/REPORT.md` is rendered from the same lab `results.json`.

## What is measured

| Field | Meaning |
| --- | --- |
| identity series | Prometheus series per metric, counting each label set once |
| complete family series | identity series plus histogram buckets, sums and counts |
| retained values | distinct values of each unbounded attribute that reached Prometheus |

Each case is compared against a `baseline` Collector with the processor removed,
on the same stack.

## Result

Source: [`results/benchmark/results.json`](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/benchmark/results.json)
and its rendered
[`REPORT.md`](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/benchmark/REPORT.md).

| Unique input values | `baseline` | `tag_only` | `overflow_attribute` | `strip_and_reaggregate` |
| --- | --- | --- | --- | --- |
| 100 | 100 | 100 | 24 | 24 |
| 1000 | 1000 | 1000 | 24 | 24 |
| 10000 | 10000 | 10000 | 24 | 24 |

The cost of one unbounded attribute is linear: one identity series per value,
10000 series for 10000 values on a Delta Sum. `tag_only` changes nothing the
backend pays for. Both enforcing modes held the metric at 24 series whatever the
input, a 99.8% reduction at 10000 values.

The reduction depends on the metric shape. Of the eight OTLP shapes at 200
unique values, only `delta_sum` and `gauge` fell to 24; cumulative sums,
histograms, exponential histograms and summaries stayed at 200 identity series
in every mode, and a classic histogram stayed at 2200 family series. That is the
documented fallback, measured.

The reduction also depends on time. A burst of 2000 new values in one epoch fell
to 24 series. The same identities replaced 200 at a time over three epochs kept
424 of 600, because each epoch is compared with the previous one and a full
churn shows no growth.

What this does to a bill: an enforcing mode caps a Delta Sum or a Gauge, and caps
nothing else. A cumulative counter or a histogram with an unbounded label needs
a different fix, which is L3-02.

## What this does not say

Measured on one host with Docker Compose, a threshold of 20 and an epoch of 10
seconds; neither was swept. Cardinality is estimated upstream with
HyperLogLog++, so counts near the threshold vary between runs. Series counts
are what Prometheus stored, not a vendor's bill. CPU and memory in the lab are
diagnostic samples on a shared host, not an overhead measurement.
