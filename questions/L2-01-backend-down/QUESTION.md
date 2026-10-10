---
id: L2-01
title: What happens to a burst when the backend is down and the queue is on?
level: 2
theme: collector
status: measured
---

## Question

L0-03 stopped the downstream collector with `sending_queue` and
`retry_on_failure` disabled, and the failure travelled all the way back: the
OTLP receiver answered 503 and counted the batch as refused. Nobody runs that
way. With the defaults on, the queue sits between the receiver and the
destination, and the question becomes what the sender is told, what the
counters say, and whether the data survives.

Two failures that look the same from a diagram are different on the wire. A
paused peer holds the TCP connection open and stops reading, so each attempt
has to reach its timeout. A stopped peer refuses the connection, so each
attempt fails at once. This question runs both, recovers from both after 20
seconds, and also runs a stop that never recovers.

## What the documentation says

On the defaults:

> - `retry_on_failure`
>   - `enabled` (default = true)
>   - `initial_interval` (default = 5s): Time to wait after the first failure
>     before retrying
>   - `max_elapsed_time` (default = 300s): Is the maximum amount of time spent
>     trying to send a batch

> - `sending_queue`
>   - `enabled` (default = true)
>   - `sizer` (default = requests): How the queue and batching is measured.
>   - `queue_size` (default = 1000): Maximum size the queue can accept.
>     Measured in units defined by `sizer`

On what a failure does:

> **Failure behavior**: If data cannot be added to the sending queue, it is
> typically dropped. This occurs when the queue has reached its configured
> capacity or, for persistent queues, when the underlying storage cannot
> accept additional data.

> If data is rejected before entering the queue, it does not reach the exporter
> retry logic. Such enqueue failures are reported by the
> `otelcol_exporter_enqueue_failed_*` metrics.

Source: [`exporter/exporterhelper/README.md`, lines 12-16, 21-30, 33 and 37](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.162.0/exporter/exporterhelper/README.md?plain=1#L12-L37),
`opentelemetry-collector v0.162.0`.

Both quotes checked against the raw file at the reference above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/v0.162.0/exporter/exporterhelper/README.md | grep -n 'max_elapsed_time\|queue_size (default\|Failure behavior\|does not reach the exporter retry logic'
```

What the page leaves open is the case an operator actually meets. It documents
the metric for data rejected *before* the queue, and says nothing about the
metric for data that entered the queue, was retried for up to 300 seconds, and
was then abandoned. It does not say what the receiver returns to the sender
while the destination is unreachable, so it does not say whether the sender can
know. And `queue_size` is given in units of `sizer`, with `requests` as the
default, which means the documented capacity of 1000 is not 1000 spans or 1000
datapoints, and the page does not spell out what that is worth in telemetry.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `sink-up` | 200, 100 accepted, 0 refused, 100 delivered, no `send_failed` series at all, queue size never above 1 |
| `sink-paused` | **200** and 100 accepted, because the queue takes the request and the receiver answers before the export is tried; failures appearing only after the 5s exporter timeout, so 2 to 4 failed attempts inside the 20s hold; all 100 delivered within one retry interval of the unpause |
| `sink-stopped-restarted` | the same 200 and the same 100 delivered, but failures from the first attempt, because a refused connection does not wait for the timeout, so a higher `send_failed` than the paused run |
| `sink-stopped` | **200** and 100 accepted with 0 arriving, `send_failed` climbing while the run lasts, and no counter saying the data is lost, because `max_elapsed_time` is 300s and the run ends first |

The prediction the author cares about is the HTTP status. L0-03 measured a 503
with the queue off; with the queue on the author expects 200 in all four
variants, which means the default configuration converts a delivery failure
into a sender-side success. If that holds, no client-side error rate can
detect a broken backend, and the only evidence is inside the Collector.

Two more predictions, both about reading the counters:

- `otelcol_exporter_send_failed_metric_points` counts datapoints per failed
  attempt, not datapoints lost, so the author expects it to exceed 100 in the
  failing variants and to end up larger in `sink-stopped` than in either
  recovered run. A number above the burst is the proof that it cannot be read
  as a loss count.
- `Queue size max` reads 1, not 100, because the default `sizer` is `requests`
  and the burst is one request. The documented capacity of 1000 is therefore
  1000 requests, which at this batch size is 100,000 datapoints, and an
  operator sizing the queue in datapoints is off by two orders of magnitude.

The author expects `Enqueue failed` to be 0 or absent everywhere: nothing here
fills a 1000-request queue.

## How to run

```bash
just ask L2-01
```

Four variants, three repetitions each. Each run posts one burst of 100
datapoints over OTLP/HTTP JSON. In the three faulting variants the fault is
applied before the burst and held for 20 seconds; two of them then recover and
the harness polls the sink until the burst arrives, recording the number of
seconds that took.

Without the harness:

```bash
COLLECTOR_CONFIG=./questions/L2-01-backend-down/config/collector-queue-defaults.yaml just up
docker compose stop sink
# post the burst, then watch the exporter give up and retry
just logs collector | tail -20
docker compose start sink
just down
```

## What is measured

Held constant: one OTLP receiver, one metrics pipeline, two exporters (`debug`
at `basic` and `otlp_grpc/sink`), and `sending_queue` and `retry_on_failure`
left at their defaults. The only setting changed from the default is
`timeout: 5s` on the exporter, so the paused case reaches its timeout inside
the 20 second hold.

| Field | Meaning |
| --- | --- |
| `sending.http_status` | what the OTLP receiver returned to the sender |
| `counters.collector.metrics.receiver_accepted` | datapoints the receiver ingested |
| `counters.collector.metrics.receiver_refused` | datapoints the receiver turned away |
| `measured.sent_to_sink` | `otelcol_exporter_sent_metric_points` filtered to the `otlp_grpc/sink` exporter |
| `counters.collector.metrics.exporter_send_failed` | datapoints the exporter records as failed; this question tests whether retries increment it |
| `measured.enqueue_failed` | `otelcol_exporter_enqueue_failed_metric_points`, the metric the documentation names for pre-queue rejection |
| `measured.queue_size_max` | `max_over_time` of `otelcol_exporter_queue_size` over the run |
| `measured.queue_capacity` | `otelcol_exporter_queue_capacity`, to read the default in the units the Collector uses |
| `counters.sink.metrics.receiver_accepted` | what reached the second collector, `-` while that target is not scrapeable |
| `measured.sink_up` | Prometheus target availability for the sink |
| `measured.drain_seconds` | seconds from lifting the fault until the sink had counted at least 100 and stopped increasing for two polls |
| `log.warn_lines`, `log.error_lines` | the collector log, counted by level |
| `measured.recovered` | whether the fault was lifted in this variant |

`drain_seconds` is a duration and not a clock reading. Its resolution is the
1 second Prometheus scrape interval plus the 1 second poll, and the two stable
polls make it longer than time-to-first-delivery.

## Result

12 runs, 0 invalid. The table is in [`results/REPORT.md`](results/REPORT.md).

| Variant | HTTP | Accepted | Send failed | Queue max | Arrived | Sink up | Seconds to drain | Warn |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sink-up` | 200 | 100 | - | 0 | 100 | 1 | - | 0 |
| `sink-paused` | 200 | 100 | - | 1 | 100 | 1 | 3 (2 to 6) | 0 |
| `sink-stopped-restarted` | 200 | 100 | - | 1 | 100 | 1 | 17 (13 to 29) | 2 (1 to 2) |
| `sink-stopped` | 200 | 100 | - | 1 | - | 0 | - | 2 |

Queue capacity read 1000 in every run. `Send failed` is `-`, meaning the
series did not exist.

The main prediction held: 200 in all four variants, including the one where
the sink target stayed down and no successful sink export was recorded. With
the queue at its default the sender is told the data was accepted, and
L0-03's 503 was an artefact of disabling the queue.

The prediction about `send_failed` was wrong, and wrong in the direction that
matters. The author expected it above 100, inflated by retries;
`otelcol_exporter_send_failed_metric_points` was never published, in any
variant. The exports did fail, and the proof is in the saved logs rather than
in a counter:

```bash
rg -c 'Exporting failed' questions/L2-01-backend-down/results/logs/sink-stopped-r1.log
```

Two per paused run, three or four per stopped run, every one of them at `info`.
The counter counted none of those retry attempts during the 20 second hold.
That is consistent with a counter that changes only after the retry budget is
exhausted, but this run does not establish when it appears. A paused run
therefore ends with zero `warn` and zero `error` lines while every export is
timing out. The stopped runs have one or two `warn` lines from the gRPC client,
not from the exporter.

The predictions about the two faults held. A paused peer gives
`DeadlineExceeded` after the 5 second timeout, which fits two attempts in the
20 second hold; a stopped peer gives `Unavailable` at once and fits three or
four. After recovery, the paused sink reached 100 and stayed there sooner than
the restarted sink in all three repetitions. The table reports drain time,
including two stable polls, rather than time to first delivery. The saved logs
record the jittered retry intervals for each run.

The prediction about the queue units held: `Queue size max` is 1 and
`Queue capacity` is 1000, both in requests, so one burst of 100 datapoints
takes one slot in a thousand.

What this does to an alert. The HTTP status is 200 and
`otelcol_exporter_send_failed_*` does not exist during the measured outage.
The paused case also has no `warn` or `error` line. The signals that moved are
`otelcol_exporter_queue_size`, at 1 for the length of the outage, and
Prometheus `up` for the sink, at 0 while it remains stopped. Alert on
destination availability, on its arrival rate while it is scrapeable, and on
a queue that is not draining. Treat the exporter failure counter as a late
signal whose behaviour after the retry budget expires remains unmeasured.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one burst of 100 datapoints, a 20
second outage and `timeout: 5s` on the exporter. Every run ended well before
`max_elapsed_time` of 300 seconds, so nothing here says what happens when the
retry budget runs out: whether `otelcol_exporter_send_failed_metric_points`
appears at that point, and whether anything is logged above `info`, is the
obvious next question and is not answered. Until it is, the sentence to use is
"the counter was absent during the measured 20 second outage", not "the counter
does not work".

The stopped sink has no current accepted-counter sample: `Sink target up` is
0 and `Arrived at the sink` is `-`, not a measured zero.

The queue was never filled. One request against a capacity of a thousand means
`block_on_overflow`, `otelcol_exporter_enqueue_failed_*` and the documented
"typically dropped" path were never exercised. The `Enqueue failed` column is
`-` when the series is absent, so this run says nothing about its first
non-zero value.

The queue is in memory, and the Collector was never restarted during the
outage. A persistent queue is a different component, and a restart would have
lost the burst this configuration kept. One burst is also far below any batch
or memory limit, so this is not evidence about an outage under load.
