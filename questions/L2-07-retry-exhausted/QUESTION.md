---
id: L2-07
title: What becomes visible when the exporter stops retrying?
level: 2
theme: collector
status: measured
---

## Question

L2-01 ended after a 20 second outage. The sender received 200, the queue held
one request, retry notices were logged at `info`, and
`otelcol_exporter_send_failed_metric_points` did not exist. The documented
default retry budget is 300 seconds, so that experiment stopped before the
exporter made its final decision.

This question keeps the destination unavailable until the default budget
expires. It asks when the queue releases the request, when `send_failed`
appears, what the final log level is, and whether any of those surfaces says
plainly that 100 accepted datapoints were abandoned.

## What the documentation says

On the retry defaults:

> - `retry_on_failure`
>   - `enabled` (default = true)
>   - `initial_interval` (default = 5s): Time to wait after the first failure
>     before retrying; ignored if `enabled` is `false`
>   - `max_interval` (default = 30s): Is the upper bound on backoff; ignored if
>     `enabled` is `false`
>   - `max_elapsed_time` (default = 300s): Is the maximum amount of time spent
>     trying to send a batch; ignored if `enabled` is `false`. If set to 0, the
>     retries are never stopped.

On failure before the retry loop:

> If data is rejected before entering the queue, it does not reach the exporter
> retry logic. Such enqueue failures are reported by the
> `otelcol_exporter_enqueue_failed_*` metrics.

Source: [`exporter/exporterhelper/README.md`, lines 12-16 and 37](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/exporterhelper/README.md?plain=1#L12-L37),
`opentelemetry-collector v0.162.0`, commit
`62cdad2ea133239380b44d20d84eb26e114779b6`.

Both quotes checked against the raw file at that tag:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/exporterhelper/README.md | grep -n 'max_elapsed_time\|does not reach the exporter retry logic'
```

The page names the retry deadline and the metric for failure before the queue.
It does not say what is emitted after data entered the queue and exhausted the
retry budget. In particular, it does not say whether `send_failed` counts
attempts or abandoned items, when the queue gauge drops, or which log level
records the final loss.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `sink-up` | HTTP 200, 100 accepted, 100 sent to the sink, no `send_failed`, queue size max 0 and no retry or drop log |
| `sink-stopped` | HTTP 200 and 100 accepted; queue size max 1; `send_failed` absent during retries and becoming exactly 100 between 300 and 330 seconds; queue size 0 afterwards; sink `up` 0; one final `error` log saying the batch was dropped |

The prediction that decides how an alert should work is the transition at
exhaustion. The author expects `send_failed` to count the 100 datapoints once,
when retry gives up, rather than once per failed attempt. If it exceeds 100,
the counter is an attempt counter. If it remains absent after 330 seconds, it
does not expose final loss under this configuration.

The author expects the queue gauge to provide the early signal and
`send_failed` plus one error log to provide the final signal. The sender should
remain unable to distinguish the loss because the queue accepted the request
before any export attempt.

## How to run

```bash
just ask L2-07
```

Two variants, three repetitions each. The stopped variant waits up to 330
seconds for `send_failed`, so this is a long-running question. Each run sends
one OTLP/HTTP request containing 100 metric datapoints. The sink is stopped
before that request and is never restarted inside the run.

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the status the receiver returned |
| `counters.collector.metrics.receiver_accepted` | datapoints accepted before export |
| `counters.collector.metrics.receiver_refused` | datapoints refused by the receiver |
| `measured.sent_to_sink` | successful datapoints attributed to the `otlp_grpc/sink` exporter |
| `counters.collector.metrics.exporter_send_failed` | the final value of `otelcol_exporter_send_failed_metric_points` |
| `measured.send_failed_at_exhaustion` | the first non-zero value observed while polling |
| `measured.queue_size_max` | maximum queued requests during the last ten minutes |
| `measured.queue_size` | queued requests after exhaustion or delivery |
| `measured.sink_up` | Prometheus target availability for the sink |
| `measured.retry_exhausted_seconds` | monotonic duration from the accepted request to the first non-zero `send_failed` sample |
| `measured.retry_log_lines` | sampled `Will retry` lines in the collector log |
| `measured.drop_log_lines` | collector log lines containing `Dropping data.` |
| `log.warn_lines`, `log.error_lines` | collector log entries counted by level |

Held constant: one request of 100 datapoints, `timeout: 5s`, the default
in-memory queue and retry settings, and `otelcol-contrib 0.162.0`. Only sink
availability varies. Collector logs are sampled, so retry-line counts are a
floor: the Collector's logger can suppress repeated messages before Compose
captures its complete output. The counter and monotonic duration carry the
result.

## Result

6 runs, 0 invalid. The table is in
[`results/REPORT.md`](results/REPORT.md).

| Variant | HTTP | Accepted | Sent to sink | Send failed | Queue max | Queue end | Sink up | Seconds to exhaustion | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sink-up` | 200 | 100 | 100 | - | 0 | 0 | 1 | - | 0 |
| `sink-stopped` | 200 | 100 | 0 | 100 | 1 | 0 | 0 | 281 (265 to 300) | 1 |

The central prediction held. `send_failed` first appears with exactly 100 in
all three stopped runs, not once per retry attempt. The queue reaches 1 while
the request is waiting and returns to 0 when the exporter gives up. The
receiver still reports HTTP 200, 100 accepted and 0 refused, so the sender
cannot distinguish final loss from successful delivery.

The log prediction held too. Each stopped run records one `error` line:

```text
Exporting failed. Dropping data.
error: no more retries left
dropped_items: 100
```

Before it, 12 or 13 `Will retry` lines are logged at `info`. The first of the
three `warn` lines appears before those retries and comes from gRPC transport
state rather than from the exporter declaring data lost. A generic warning
alert can therefore fire early on connection noise; the exporter-scoped error
arrives only at final loss.

The timing prediction failed. The author expected `send_failed` between 300
and 330 seconds; the first non-zero sample appeared after 265, 281 and 300
seconds. The documented `max_elapsed_time: 300s` is an upper bound in these
runs, not a promise that attempts continue for 300 seconds.

What this does to an alert: queue size is the early signal that delivery is
waiting, `send_failed` is the final-loss counter, and the error log is the
final-loss event with the dropped-item count. HTTP status says only that the
queue admitted the request.

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
