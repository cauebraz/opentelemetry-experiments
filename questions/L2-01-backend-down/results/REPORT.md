# L2-01 What happens to a burst when the backend is down and the queue is on?

Level 2, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L2-01`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `sink-up` | the sink reachable throughout |
| `sink-paused` | the sink paused for 20s, which holds the connection open |
| `sink-stopped-restarted` | the sink stopped for 20s, then started again |
| `sink-stopped` | the sink stopped before the burst and never started |

## Measured

| Variant | HTTP status | Accepted | Refused | Sent to the sink | Send failed | Enqueue failed | Queue size max | Queue capacity | Arrived at the sink | Sink target up | Seconds to drain | Warn lines | Error lines | Recovered |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sink-up` | 200 | 100 | 0 | 100 | - | - | 0 | 1000 | 100 | 1 | - | 0 | 0 | not applicable |
| `sink-paused` | 200 | 100 | 0 | 100 | - | - | 1 | 1000 | 100 | 1 | 3 (2 to 6) | 0 | 0 | yes |
| `sink-stopped-restarted` | 200 | 100 | 0 | 100 | - | - | 1 | 1000 | 100 | 1 | 17 (13 to 29) | 2 (1 to 2) | 0 | yes |
| `sink-stopped` | 200 | 100 | 0 | - | - | - | 1 | 1000 | - | 0 | - | 2 | 0 | no |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

With the queue and the retry at their defaults, a backend outage is invisible
to the sender and to the export-failure counter during the measured window.

The HTTP status is 200 in all four variants, including the run where the sink
target is down and no successful sink export is recorded. The receiver accepts
100 and refuses 0 in every variant. L0-03 measured a 503 and 100 refused for
the same outage with the queue disabled, so turning the queue on, which is the
default, converts a delivery failure into a sender-side success. No client
error rate can detect this.

`otelcol_exporter_send_failed_metric_points` was never published. Not in the
paused run, not in the run that was stopped and restarted, and not in the run
where the sink remained unavailable and no successful export was recorded.
The exports did fail: the collector log carries two
`Exporting failed. Will retry the request after interval.` lines in each
paused run and three or four in each stopped run. The counter counts none of
those retry attempts during the 20 second hold. That is consistent with a
counter that changes only after the retry budget is exhausted, but this run
does not establish when it appears. An alert on the counter would not have
fired during the measured outage.

The log does not help either. Those retry lines are logged at `info`. A
paused run ends with zero `warn` and zero `error` lines, so a dashboard
counting log levels sees a quiet collector while every export is timing out.
The stopped runs produce one or two `warn` lines, and they come from the gRPC
client ("connect: connection refused"), not from the exporter. One detail
worth knowing: the retry line carries a field named `error`, so a text search
for the word finds it while a level filter does not.

Two faults that look the same in a diagram behave differently and both end up
delivering. A paused peer holds the connection and each attempt dies on the
exporter's 5 second timeout with `DeadlineExceeded`, which fits two attempts
into the 20 second hold. A stopped peer refuses the connection, each attempt
fails at once with `Unavailable`, and three or four fit in the hold. After
recovery, the paused sink reached 100 and stayed there sooner than the
restarted sink in all three repetitions. The saved log records every jittered
interval, so the retry schedule can be inspected without treating the count of
log levels as the evidence.

`Queue size max` reads 1 and `Queue capacity` reads 1000. Both are in
requests, because the default `sizer` is `requests`, so the burst of 100
datapoints occupies one slot of a thousand. Sizing that queue by thinking in
datapoints overstates the buffer by whatever the batch size turns out to be.

The practical conclusion is that sender status and `send_failed` are not enough
to detect this. The signals that moved were
`otelcol_exporter_queue_size`, a Collector gauge sitting at 1 for the length
of the outage, and Prometheus `up` for the destination, which read 0 when the
sink stayed stopped. Alert on destination availability, on its arrival rate
while it is scrapeable, and on a queue that is not draining. Do not rely on
`send_failed` for the first 20 seconds, which is the only interval measured
here.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one burst of 100 datapoints, a
20 second outage and `timeout: 5s` on the exporter. Every run ended well before
`max_elapsed_time` of 300 seconds, so this says nothing about what the
Collector does when the retry budget runs out: whether
`otelcol_exporter_send_failed_metric_points` appears then, and whether anything
is logged above `info`, is the obvious next question and is not answered here.
The stopped sink has no current accepted-counter sample; `Sink target up` is 0,
and `Arrived at the sink` is `-`, not a measured zero.

The queue was never filled. One request against a capacity of a thousand means
`block_on_overflow`, `otelcol_exporter_enqueue_failed_*` and the documented
"typically dropped" behaviour were never exercised. The `Enqueue failed`
column is `-` everywhere because the series was absent. This run does not show
what value it takes after the first enqueue rejection.

The queue is in memory. A persistent queue backed by a storage extension is a
different component with different guarantees, and a Collector restarted
during the outage would have lost the burst that this configuration kept.
