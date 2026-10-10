---
id: L3-02
title: Which histogram reduction keeps the percentile you were asking for?
level: 3
theme: metrics
status: measured
evidence_repository: https://github.com/cauebraz/opentelemetry-cardinality-lab
evidence_commit: ad5779d31d33c876f84d5e2bf164d427b89b356e
evidence_results: https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/histograms/results.json
---

## Question

L3-01 showed the Cardinality Guardian leaving a histogram at its input series
count. Something else has to reduce it: a view in the SDK, a transform or a
filter in the Collector, routing, or exponential buckets. Each removes series,
and each can stop answering a question the histogram was created to answer. This
question names, for each option, the series it removes and the question it
loses.

## What the documentation says

> Reaggregation is only safe for Delta Sums and Gauges; Cumulative Sums, Histograms, ExponentialHistograms, and Summaries require stateful tracking that this processor doesn't perform, so they fall back to `tag_only` regardless of the configured mode.

Source: [`processor/cardinalityguardianprocessor/README.md`, line 160](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/cardinalityguardianprocessor/README.md?plain=1#L160),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`.

Checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/cardinalityguardianprocessor/README.md | grep -n 'Reaggregation is only safe for Delta Sums and Gauges'
```

The processor documents what it will not do with a histogram. Nothing in it, or
in the pages of the alternatives, says which percentile each alternative keeps.

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
just benchmark-histograms
```

33 runs, eleven strategies, three repetitions each. One delta histogram carries
200 unique `user.id` values.

## What is measured

| Field | Meaning |
| --- | --- |
| identity series | Prometheus series counting each label set once |
| total series | identity series plus buckets, sums and counts |
| `total_requests` | an integrity check: the request count the histogram must preserve |
| `p95_by_method`, `requests_by_status` | questions that do not need the user |
| `p95_for_one_user` | the question every identity-reducing option is expected to lose |

A query that returns nothing is recorded as `null`.

## Result

Source: the lab's [`results/histograms/results.json`](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/histograms/results.json)
and its rendered
[`REPORT.md`](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/histograms/REPORT.md).

| Strategy | Identity series | Total series | `total_requests` | `p95_by_method` | `p95_for_one_user` |
| --- | --- | --- | --- | --- | --- |
| `baseline` | 200 | 2200 | 600 | 9.107 | 4.750 |
| `transform-delete` | 20 | 220 | 60 | 9.375 | null |
| `transform-aggregate` | 20 | 220 | 600 | 9.107 | null |
| `filter` | 0 | 0 | null | null | null |
| `sdk-view` | 20 | 220 | 600 | 9.107 | null |
| `exponential` | 200 | 600 | 600 | null | null |
| `exponential-native` | 200 | 200 | 600 | 6.999 | 1.000 |

Three options removed the user and kept the rest. `transform-aggregate` and an
SDK `sdk-view` cut 2200 series to 220, kept all 600 requests, kept
`p95_by_method` at 9.107, and lost only `p95_for_one_user`.

One option changed the answer instead of trading a question. `transform-delete`
also produced 220 series but kept 60 of 600 requests and logged 11
`Dropped misaligned histogram datapoint` lines per run. Its `p95_by_method` of
9.375 is a different number, not a cheaper one.

One option reduced series without losing a question. Exponential buckets scraped
as native histograms kept 200 identities in 200 series, against 2200, and still
answered the per-user percentile. Scraped as text they kept a single `+Inf`
bucket and answered no percentile at all.

What this does to a bill: the decision rule is to remove the identity in the SDK
or with an aggregating transform when nobody asks the per-identity question,
and to move to native exponential histograms when somebody does. Deleting the
key in a transform is the option that silently changes the totals.

## What this does not say

Measured on one host with one synthetic histogram whose values repeat across
identities, so percentile cells show whether a question can be answered, not
what a real distribution looks like. The queries read buckets directly rather
than through `rate()`, because each run is one burst into a fresh stack. Only
the `exponential-native` strategy enabled native histogram scraping. Log line
counts are floors under the Collector's sampler.
