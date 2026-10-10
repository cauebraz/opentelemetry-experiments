# L2-07 What becomes visible when the exporter stops retrying?

Level 2, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L2-07`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `sink-up` | the sink remains reachable |
| `sink-stopped` | the sink stays stopped past the default 300s retry budget |

## Measured

| Variant | HTTP status | Accepted | Refused | Sent to the sink | Send failed | Failed at exhaustion | Queue size max | Queue size at end | Sink target up | Seconds to exhaustion | Retry log lines | Drop log lines | Warn lines | Error lines | Exhaustion observed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sink-up` | 200 | 100 | 0 | 100 | - | - | 0 | 0 | 1 | - | 0 | 0 | 0 | 0 | not applicable |
| `sink-stopped` | 200 | 100 | 0 | 0 | 100 | 100 | 1 | 0 | 0 | 281 (265 to 300) | 13 (12 to 13) | 1 | 3 | 1 | yes |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The sender never learns the final outcome. It receives HTTP 200, and the
receiver records 100 accepted and 0 refused, in both variants. With the sink
up, all 100 are sent. With the sink stopped, the successful-send counter for
that exporter reads 0 and Prometheus `up` reads 0.

The transition L2-01 could not see happens once, at the end. During the first
20 seconds L2-01 saw no `otelcol_exporter_send_failed_metric_points`; here its
first non-zero value is exactly 100 in every repetition. It does not count
each retry attempt in this configuration. It counts the 100 datapoints the
exporter finally abandons. At the same transition the queue moves from its
maximum of 1 request to 0.

The final log is also unambiguous. Every stopped run has one `error` line:
`Exporting failed. Dropping data.`, with `dropped_items` equal to 100 and
`no more retries left` in the error field. The 12 or 13 preceding retry
notices are at `info`. The first of the three `warn` lines appears before those
retries and comes from gRPC transport state. A generic warning alert can
therefore fire early on connection noise; the exporter-scoped error arrives
only at final loss.

The timing prediction was too narrow. The author expected the first
`send_failed` sample between 300 and 330 seconds. It appeared after 265, 281
and 300 seconds. The documented 300 seconds behaves as a maximum retry budget,
not as a promise that retries continue for the whole interval.

For an alert, `otelcol_exporter_queue_size > 0` is the early signal,
`otelcol_exporter_send_failed_metric_points` is the final-loss counter, and
the error log is the final-loss event with the dropped-item count. The sender's
HTTP status remains a record of queue admission, not delivery.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one in-memory queue request carrying
100 metric datapoints, an OTLP gRPC destination, `timeout: 5s` and the default
retry settings. Concurrent requests, a full queue, a persistent queue and a
Collector restart are different failure paths.

The one-second poll records the first Prometheus sample in which `send_failed`
is non-zero, not the Collector's internal deadline to sub-second precision.
The 265 to 300 second range proves only that `max_elapsed_time: 300s` is an
upper bound in these runs. It does not identify which backoff decision ends a
particular run early.

The sink is never recovered after exhaustion, so this does not test whether
data can be rescued near the deadline. Once the queue reaches 0 and the error
records `dropped_items: 100`, this configuration has no persistent copy to
deliver later.
