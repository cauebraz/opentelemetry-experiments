# L0-02 What survives when a metric or a log is missing a field?

Level 0, theme metrics. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L0-02`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `delta-sum` | a monotonic sum with delta temporality |
| `sum-without-temporality` | the same sum with no aggregationTemporality field |
| `gauge` | a gauge, which has no temporality to leave out |
| `log-without-timestamp` | a log record with no timeUnixNano |
| `log-with-trace-id` | a log record with a timestamp and a trace id |

## Measured

| Variant | HTTP status | Accepted points | Accepted logs | Sink points | Sink logs | Exported series | Printed by debug | Warn lines | Timestamp is 1970 | Trace id hex chars |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `delta-sum` | 200 | 1 | - | 1 | - | 1 | 1 | 0 | - | - |
| `sum-without-temporality` | 200 | 1 | - | 1 | - | 0 | 1 | 0 | - | - |
| `gauge` | 200 | 1 | - | 1 | - | 1 | 1 | 0 | - | - |
| `log-without-timestamp` | 200 | - | 1 | - | 1 | - | 1 | 0 | yes | 0 |
| `log-with-trace-id` | 200 | - | 1 | - | 1 | - | 1 | 0 | no | 32 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Every one of the five payloads came back 200. None of them was refused, and
the collector log carried zero warnings in all fifteen runs.

The sum with no `aggregationTemporality` was accepted as one metric point,
reached the sink as one metric point, and produced zero series on the
Prometheus exporter. The delta sum and the gauge each produced one. So the
datapoint crossed the whole pipeline, was counted as delivered by every
counter on the way, and then disappeared at the exporter with nothing written
anywhere about it.

That is the shape of the failure worth remembering. A missing series is not
visible on a dashboard, because a dashboard shows series that exist. The
alert that would have fired on the metric never fires, because there is no
metric. `otelcol_receiver_accepted_metric_points` and
`otelcol_exporter_sent_metric_points` both agree that the data moved, and the
only way to notice is to go looking for a metric you expected and find it
absent.

The two log records behaved as the protocol describes. The record with no
`timeUnixNano` was accepted and printed with the timestamp at the Unix epoch,
which places the event at 1970 for whoever stores it. The record carrying a
32 character trace id kept it intact through the pipeline, which is what makes
a log clickable from a trace.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with the `prometheus` exporter. A
different exporter can make a different decision about a sum with no
temporality, and a vendor OTLP endpoint is free to accept and store it. One
datapoint per request says nothing about a batch where one point out of a
thousand is malformed, nor about whether the drop would be visible at that
ratio. Nothing here measures what a backend does with a log record it stored
at the Unix epoch, only that the Collector hands it over that way.
