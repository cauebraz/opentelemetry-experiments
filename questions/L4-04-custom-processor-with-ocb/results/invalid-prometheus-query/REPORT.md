# L4-04 What does it take to run a processor you wrote yourself?

Level 4, theme collector. 1 repetition per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L4-04`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `stock-pass-through` | otelcol-contrib, batch only |
| `custom-pass-through` | custom distribution, batch only |
| `custom-processor` | custom distribution with the experiment processor |

## Measured

| Variant | HTTP | Delivered | Stamped | Kept | Startup s | Idle RSS | Heap | Post s | CPU ns | Stock reject | Custom accept | Binary B | Image B | Build s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `stock-pass-through` | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| `custom-pass-through` | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| `custom-processor` | - | - | - | - | - | - | - | - | - | - | - | - | - | - |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Invalid runs

| Variant | Repetition | Why |
| --- | --- | --- |
| `stock-pass-through` | 1 | the sink accepted None |
| `custom-pass-through` | 1 | the sink accepted None |
| `custom-processor` | 1 | the sink accepted None |

## Interpretation

Placeholder until the first valid session is recorded.

## What this does not say

Placeholder until the first valid session is recorded.
