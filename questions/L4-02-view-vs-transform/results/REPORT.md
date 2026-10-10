# L4-02 Should the fix live in the SDK or in the platform?

Level 4, theme sdk, metrics. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L4-02`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `baseline` | full attributes, no SDK view, no Collector transform |
| `sdk-view` | SDK View attribute_keys, baseline Collector config |
| `collector-transform` | full attributes, aggregate_on_attributes in the Collector |

## Measured

| Variant | SDK request B | Collector RX B | SDK wall s | SDK CPU s | Collector CPU ns | Inbound user.id values | Inbound with user.id | Identity series | Family series | Observations | p95 overall | user.id after | Sink datapoints | Exports |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `baseline` | 63054 | 63379 (63265 to 63504) | 0.010 (0.009 to 0.011) | 0.007 | 5360209 (3611476 to 6966842) | 200 | 200 | 200 | 2200 | 600 | 6.643 | True | 200 | 1 |
| `sdk-view` | 6070 | 5974 (5968 to 6261) | 0.007 | 0.005 (0.004 to 0.005) | 3661299 (3592727 to 4779991) | 0 | 0 | 20 | 220 | 600 | 6.643 (6.643 to 6.659) | False | 20 | 1 |
| `collector-transform` | 63054 | 63383 (63376 to 63646) | 0.012 (0.012 to 0.013) | 0.007 | 5844679 (5187495 to 6232202) | 200 | 200 | 20 | 220 | 600 | 6.643 (6.643 to 6.659) | False | 20 | 1 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Both reductions produced the same Prometheus answer: 20 identity series and
220 family series, 600 observations, and the same per-method p95. The
difference is what each side still knew after the cut.

The SDK View never showed `user.id` to the platform. The inbound count
connector saw 20 datapoints under the default `none`, and the SDK request
shrank from 63054 bytes to 6070, about one tenth. The Collector transform
paid the full 63054 byte payload first: its inbound count saw all 200
`user.id` values, then removed them before export. A fan-out that needs the
user, or a second pipeline that does, is still possible only on the
Collector path.

SDK wall time was not higher with the View. Emitting and flushing took
0.007 s with the View against 0.010 to 0.013 s without it, because the
exporter encoded 20 histogram points instead of 200. Collector CPU over the
window was in the same order for every variant on this small burst; the
clear cost signal is the bytes on the SDK-to-Collector hop, not the
nanoseconds inside either process.

What this does to an operation: put the View in the application when the
attribute must never leave the process and a release of that application is
acceptable. Keep the aggregating transform in the Collector when the
platform must still see the attribute, change the allow-list without an
application release, or fan the raw stream out before it is reduced. Deploy
time is not measured here; ownership and recoverability are.

## What this does not say

Measured with `opentelemetry-sdk 1.45.0` and
`opentelemetry-exporter-otlp-proto-http 1.45.0` on Python 3.14, one Delta
histogram, 600 observations, and a single force-flush. Continuous scrapes,
multi-service fan-out, and the calendar time to ship an SDK change versus a
Collector config change are outside the run.

The transform config normalizes datapoint timestamps before
`aggregate_on_attributes`. Without that step the Prometheus exporter of
`otelcol-contrib 0.162.0` drops histogram points whose starts differ after
the attributes collapse. That interaction is part of the Collector path's
operational cost, not a claim about every backend.

Collector CPU on a burst this small is noisy across Docker Desktop on
Darwin arm64; treat the byte counts and the inbound count connector as the
stable evidence.
