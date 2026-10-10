---
id: L0-00
title: One sentence, in the form of a question
level: 0
theme: collector
status: stub
---

## Question

One paragraph. What is being asked, and why the answer is not obvious from
reading the configuration.

## What the documentation says

The paragraph this question tests, quoted, with its URL and the version it was
read at.

> Quote the sentence here.

Source: https://opentelemetry.io/docs/... read at `otelcol-contrib 0.162.0`.

Say in one line what the paragraph leaves open. A question whose answer is
already written there, without ambiguity, does not belong in this repository.

## Hypothesis

Written before the first run and never edited afterwards.

| Variant | What the author expected |
| --- | --- |
| `variant-name` | the prediction, with a number when one can be predicted |

## How to run

```bash
just ask L0-00
```

Add the single-command equivalent when there is one, so the question can be
reproduced without the harness.

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the status the receiver returned |

Say here what is held constant and what varies.

## Result

Written by the harness into [`results/REPORT.md`](results/REPORT.md), and
summarized in one or two sentences here after the first run, including whether
the hypothesis held.

## What this does not say

The limits of the measurement, in two or three sentences. Version, scale,
anything that would plausibly change the answer.
