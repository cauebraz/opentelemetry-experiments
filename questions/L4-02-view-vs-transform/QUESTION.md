---
id: L4-02
title: Should the fix live in the SDK or in the platform?
level: 4
theme: sdk, metrics
status: measured
---

## Question

A view in the SDK and a transform in the Collector can produce the same series.
They differ in who owns the change, how long it takes to ship, what is
irrecoverable afterwards, and what the platform team can do without the
application team. The measurement makes those differences concrete.

## What the documentation says

On the SDK view allow-list:

> `attribute_keys`: This is, at a minimum, an allow-list of attribute keys for
> measurements captured in the metric stream. The allow-list contains attribute
> keys that identify the attributes that MUST be kept, and all other attributes
> MUST be ignored.

Source: [`specification/metrics/sdk.md`, lines 392-395](https://github.com/open-telemetry/opentelemetry-specification/blob/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/metrics/sdk.md?plain=1#L392-L395),
OpenTelemetry specification `v1.61.0`, commit
`32c0b2c1d9e11f68da0e6e7b63044e57878590de`.

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/metrics/sdk.md | sed -n '392,395p'
```

On the Collector aggregating transform:

> The optional `attributes` argument controls which datapoint attributes are
> kept before aggregation:
>
> - `[...]` (list of attribute keys): only the specified data point attributes
>   are kept. All other attributes are removed, and aggregation is performed by
>   grouping on the remaining attributes.

Source: [`processor/transformprocessor/README.md`, `aggregate_on_attributes`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/transformprocessor/README.md?plain=1#L554-L558),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`.

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/transformprocessor/README.md | sed -n '554,558p'
```

Both pages say which attributes survive. Neither says what that costs on the
wire, at the SDK, or at the Collector, or whether the platform still saw the
attribute the application dropped.

## Hypothesis

Three variants emit the same 600 histogram observations (200 `user.id` values,
three each) with the same method and status distribution. `baseline` keeps
every attribute. `sdk-view` applies an SDK View with
`attribute_keys={environment, region, http.method, http.status_code}` before
export. `collector-transform` sends the full attributes and runs
`aggregate_on_attributes("sum", [...])` on the same keys.

Predicted:

1. `baseline` produces 200 identity series and 2200 Prometheus family series.
   Both reductions produce 20 identities and 220 family series.
2. Every variant keeps sum and count at 600 observations and the same
   `p95_by_method` across the preserved dimensions.
3. `sdk-view` never presents `user.id` to the Collector: the inbound count
   connector sees only the default `none`. `collector-transform` presents all
   200 `user.id` values on the inbound count, then removes them before export.
4. Bytes SDK to Collector are materially lower for `sdk-view` than for the two
   Collector-side variants, which send the same full payload.
5. SDK wall time is higher for `sdk-view` than for the other two; Collector CPU
   is higher for `collector-transform` than for `sdk-view`.

A run is invalid when the observation count is not 600, when sum or count is
lost, when `p95_by_method` diverges across variants that should match, when an
unexpected identity appears, when counters disagree with the sink log, or when
a process restarts or is OOM-killed. Three repetitions.

## How to run

```bash
just ask L4-02
```

Pins: `otelcol-contrib 0.162.0`, `opentelemetry-sdk 1.45.0`,
`opentelemetry-exporter-otlp-proto-http 1.45.0`, Prometheus `v3.15.0`.

The `collector-transform` config sets one start and one end timestamp on every
datapoint before `aggregate_on_attributes`. Without that alignment the
Prometheus exporter drops histogram points whose starts differ by
microseconds after the attributes collapse, which is the same class of drop
L3-02 recorded for a deleting transform. The inbound count connector still
sees the original points, with their original `user.id` values.

## What is measured

| Field | Meaning |
| --- | --- |
| SDK request bytes | body bytes the SDK HTTP exporter sent to the Collector |
| SDK wall s, SDK CPU s | emitter process time for the observations and flush |
| Collector CPU ns | Docker Engine `total_usage` over the workload window |
| inbound identities | distinct `user.id` values on the count connector metric |
| inbound with user.id | datapoints counted under a real `user.id`, not `none` |
| identity / family series | Prometheus series of the exported histogram |
| observations | sum of the histogram `_count` |
| p95 by method | `histogram_quantile(0.95)` over preserved method dimensions |
| user.id after | whether any exported series still carries `user_id` |

## Result

Source: this question's [`results/results.json`](results/results.json). Nine valid runs, three
per variant.

| Variant | SDK request B | Identity | Family | Observations | Inbound user.id values | user.id after |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `baseline` | 63054 | 200 | 2200 | 600 | 200 | yes |
| `sdk-view` | 6070 | 20 | 220 | 600 | 0 | no |
| `collector-transform` | 63054 | 20 | 220 | 600 | 200 | no |

Predictions 1 to 4 held. Prediction 5 did not: SDK wall time was lower with
the View (0.007 s) than without it (0.010 to 0.013 s), because the exporter
encoded 20 points instead of 200. Collector CPU on this burst stayed in the
same order for every variant. The stable cost difference is the
SDK-to-Collector payload, which the View cut by about 90%.

Per-method p95 matched across all nine runs (`GET`/`POST`/`DELETE` 6.643,
`PUT` 6.659). The SDK path made `user.id` irrecoverable at the platform; the
Collector path proved the platform received it, then removed it.

## What this does not say

Measured with `opentelemetry-sdk 1.45.0` on Python 3.14, one Delta histogram
and a single force-flush. How long an application release takes versus a
Collector config change is not a number this harness produces. The transform
config aligns datapoint timestamps before aggregation so the Prometheus
exporter of `0.162.0` does not drop the collapsed points; other backends are
untested.
