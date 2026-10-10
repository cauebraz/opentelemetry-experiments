---
id: L2-05
title: What does a trace look like when the exporter failed halfway through it?
level: 2
theme: traces
status: measured
---

## Question

Spans of one trace are exported in different batches. When some batches fail and
others succeed, the backend holds a trace with holes. This question measures how
many traces end up incomplete and whether anything in the pipeline could tell
you that they are.

L2-01 and L2-07 measured one request of 100 datapoints, so loss was all or
nothing. A trace is the opposite shape: five spans crossing the same exporter at
five different moments, where losing one of the five is worse than losing all
five, because the backend then renders a trace that is wrong rather than one
that is absent.

The initial phase plan placed each trace's five spans in consecutive requests.
With the outage covering requests 25 through 74, that arrangement puts five
whole traces before the outage, ten wholly inside it and five after it. It
measures missing traces, not holes. The implemented order interleaves the twenty
traces so every trace straddles the same outage; this is the workload required
by the question in the title.

## What the documentation says

> - `retry_on_failure`
>   - `enabled` (default = true)
>   - `initial_interval` (default = 5s): Time to wait after the first failure before retrying; ignored if `enabled` is `false`
>   - `max_interval` (default = 30s): Is the upper bound on backoff; ignored if `enabled` is `false`
>   - `max_elapsed_time` (default = 300s): Is the maximum amount of time spent trying to send a batch; ignored if `enabled` is `false`. If set to 0, the retries are never stopped.
>   - `multiplier` (default = 1.5): Factor by which the retry interval is multiplied on each attempt; ignored if `enabled` is `false`

> - `sending_queue`
>   - `enabled` (default = true)
>   - `num_consumers` (default = 10): Number of consumers that dequeue batches; ignored if `enabled` is `false`

Source: [`exporter/exporterhelper/README.md`, lines 12-23](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/exporterhelper/README.md?plain=1#L12-L23),
`opentelemetry-collector v0.162.0`, commit
`62cdad2ea133239380b44d20d84eb26e114779b6`.

Checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/exporter/exporterhelper/README.md | grep -n 'retry_on_failure\|num_consumers\|max_elapsed_time'
```

Every setting on that page is per batch. "The maximum amount of time spent
trying to send a batch" and "number of consumers that dequeue batches" describe
one request at a time, and nothing on the page mentions a trace, a trace id or
a parent span. A request is the unit the Collector retries and the unit it
abandons; a trace is not a unit it knows about.

The page therefore cannot say what happens to a trace whose spans are spread
across several requests with different fates. That is what this question
measures.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `sink-up` | 100 spans, 20 complete traces, no `send_failed`, no drop log |
| `retry-default` | 100 spans and 20 complete traces, the 300 second budget outlasting a 20 second outage |
| `retry-off` | 50 spans, 0 complete traces, 20 incomplete traces, `send_failed` 50, 20 roots present |
| `retry-10s` | between 50 and 100 spans, so fewer incomplete traces than `retry-off` and more than `retry-default` |

The arrival order is what makes this a question about holes. Span `j` of trace
`t` is sent as request `20j + t`, so each of the twenty traces has two or three
of its five spans inside the outage window and the rest outside. The author
expects `retry-off` to produce twenty traces that all exist and are all wrong,
rather than ten traces that are missing.

The prediction that decides whether this is detectable is about the roots. The
root span of every trace is sent in the first twenty requests, before the sink
is stopped, so the author expects all twenty roots to arrive in every variant.
A backend therefore has twenty traces it believes it has received, and the
missing children are visible only as an absence that nothing announces.

The author expects no counter to name a trace. `send_failed` should count spans,
`queue_size` should count requests, and no series should carry a trace id, so
the ratio of incomplete traces is expected to be unavailable from the
Collector's own telemetry at any level.

For `retry-10s` the author expects partial survival rather than a clean split,
because ten consumers dequeue at most ten requests at a time: a request that
waited in the queue until after recovery starts its retry clock late and
delivers, while a request picked up at the start of the outage exhausts ten
seconds and is dropped.

## How to run

```bash
just ask L2-05
```

Four variants, three repetitions each. Each run posts 100 OTLP/HTTP requests of
one span each, 0.4 seconds apart, for 40 seconds. The three faulted variants
stop the sink before request 25 and start it again before request 75, so 50 of
the 100 spans are sent while the destination is unreachable. The run then waits
until the sink's span count has been unchanged for 35 seconds, which is longer
than `max_interval`, or until all 100 spans have arrived.

The sink prints every span at `verbosity: detailed` with log sampling off, so
the trace and span ids of everything that arrived are recovered from its log.
`docker compose stop/start` restarts the sink process and resets its self
counter. The harness waits for the first 25 spans before the stop, records that
offset, and adds the post-restart counter to it. The log remains cumulative
across both process epochs.

Without the harness:

```bash
SINK_CONFIG=./config/sink-detailed.yaml \
  COLLECTOR_CONFIG=./questions/L2-05-traces-under-exporter-failure/config/collector-retry-off.yaml just up
docker compose stop sink
curl -s -o /dev/null -X POST http://127.0.0.1:24318/v1/traces \
  -H 'Content-Type: application/json' \
  -d '{"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":"5b8efff798038103d269b633813fc60c","spanId":"eee19b7ec3c1b174","parentSpanId":"aaaaaaaaaaaaaaaa","name":"step-1","kind":2,"startTimeUnixNano":"1760000000000000000"}]}]}]}'
curl -s http://127.0.0.1:28888/metrics | grep '^otelcol_exporter'
docker compose start sink
just logs sink | grep 'Trace ID'
```

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the highest status returned across the 100 requests |
| `sending.spans_sent` | spans posted, held at 100 |
| `sending.spans_during_outage` | spans posted while the sink was stopped, 50 in the faulted variants |
| `sending.spans_at_quiet` | the cumulative sink span count across both process epochs when it stopped changing |
| `sending.sink_counter_offset` | accepted spans recorded before the sink process was stopped |
| `sending.queue_at_recovery` | queued requests sampled immediately before the sink was restarted |
| `sending.drain_seconds` | monotonic seconds from restart until the last change in the cumulative sink counter |
| `counters.collector.traces.receiver_accepted` | spans accepted before export |
| `measured.sent_to_sink` | successful spans attributed to the `otlp_grpc/sink` exporter |
| `measured.send_failed` | `otelcol_exporter_send_failed_spans` |
| `measured.enqueue_failed` | `otelcol_exporter_enqueue_failed_spans` |
| `measured.queue_size_max` | maximum queued requests over the last ten minutes |
| `measured.spans_arrived` | span blocks the sink printed, counted from its log |
| `measured.traces_seen` | distinct trace ids in the sink log |
| `measured.traces_complete` | trace ids holding all five spans |
| `measured.traces_incomplete` | trace ids holding one to four spans |
| `measured.traces_missing` | trace ids that never appeared |
| `measured.trace_size_counts` | number of traces holding each observed span count, retained in this question's `results.json` |
| `measured.roots_arrived` | span blocks whose `Parent ID` is empty |
| `measured.largest_hole` | five minus the smallest number of spans seen for a trace |
| `measured.requests_at_sink` | export requests the sink logged |
| `measured.retry_log_lines` | `Will retry the request` lines in the collector log |
| `measured.drop_log_lines` | `Dropping data.` lines in the collector log |
| `measured.sink_up` | Prometheus target availability for the sink |
| `log.warn_lines`, `log.error_lines` | collector log entries counted by level |

Held constant: 20 traces of 5 spans, one span per request, 0.4 seconds apart,
the same 20 second outage at the same offset, `timeout: 5s`, the default
in-memory queue with `num_consumers: 10`, no batch processor, and
`otelcol-contrib 0.162.0`. Only `retry_on_failure` varies, and `sink-up`
additionally has no outage.

The `retry-10s` variant also sets `max_interval: 5s`. The Collector refuses to
start on `max_elapsed_time: 10s` alone, with
`'max_elapsed_time' must not be less than 'max_interval'`, and the default
`max_interval` is 30s. Shortening the budget below 30 seconds therefore means
shortening the backoff ceiling as well, which is a constraint the documented
defaults do not mention.

The collector under test keeps the default log sampling, so `retry_log_lines`
and `drop_log_lines` are floors. The trace figures come from the sink's log,
which is not sampled, so they are counts.

## Result

12 runs, 0 invalid. The table is in [`results/REPORT.md`](results/REPORT.md).

| Variant | Sent | Failed | Queue at recovery | Arrived | Complete | Incomplete | Distribution | Drain seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sink-up` | 100 | - | - | 100 | 20 | 0 | `5:20` | - |
| `retry-default` | 100 | - | 49 (48 to 50) | 100 | 20 | 0 | `5:20` | 23.4 (20.4 to 26.4) |
| `retry-off` | 48 (44 to 49) | 52 (51 to 56) | 0 | 48 (44 to 49) | 0 | 20 | varies | 10.3 |
| `retry-10s` | 80 (78 to 80) | 20 (20 to 22) | 30 (30 to 32) | 80 (78 to 80) | 0 (0 to 1) | 20 (19 to 20) | varies | 10.3 (10.3 to 11.3) |

The central prediction held. With retry disabled the outage did not lose traces,
it corrupted all of them: all twenty traces arrived and none was complete.
Across the repetitions, 11 to 16 traces held 2 of their 5 spans and 4 to 9 held
3. `trace_size_counts` carries each distribution in `results.json`.

The prediction about roots held too. All twenty root spans arrived in every
variant, because the root is sent before the outage begins. A trace list after
this incident looks normal, and the damage is two or three levels down.

The default retry absorbed the outage completely: 100 spans, 20 complete
traces, 0 error lines and 0 drop lines in all three repetitions. The queue held
48 to 50 requests immediately before recovery, peaked at 55 to 61, and the last
sink-counter change came 20.4 to 26.4 seconds after restart. The cumulative sink
counter and the unsampled log both read 100.

`retry-10s` behaved as predicted in spans and worse than predicted in traces. It
delivered 78 to 80 spans and produced 0 or 1 complete trace, with a largest hole
of 1 or 2. At recovery the queue read 30 to 32 requests, the final loss was 20
to 22 spans, and the last sink-counter change came 10.3 to 11.3 seconds after
restart. Those observations are consistent with queued requests surviving long
enough to be retried, but do not correlate a queue slot with a span.

The counters measured here do not know what a trace is. `send_failed_spans`
counted 51 to 56 spans under `retry-off`, which cannot distinguish 20 damaged
traces from a smaller number of missing ones. `queue_size` counts requests.
`enqueue_failed` was never published, because the queue peaked at 61 of its
1000 request capacity.

The early signal from L2-01 disappears in the worst case. `queue_size_max` reads
0 under `retry-off`, because with retry disabled a request fails on its first
attempt and never dwells. The gauge that sat at 1 throughout the outage in
L2-01 stays flat here while 51 to 56 spans are discarded. Queue depth warns
about delivery that is waiting, not delivery already abandoned.

What remains is the log, and it is sampled. Each dropped request produced
`Exporting failed. Dropping data. Try enabling retry_on_failure config option to
retry on retryable errors.` at `error`, with `"dropped_items": 1`. `retry-off`
recorded 21 to 26 of those lines for 51 to 56 dropped spans, so the text
undercounts under the default sampler.

What this does to an alert: alert on `send_failed_spans` for the loss and on
destination availability for the cause, and accept that neither says a trace is
incomplete. Detecting that needs the backend to compare spans against their
parents, because the Collector never held a whole trace in one place.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with 20 traces of 5 spans, one span per
OTLP/HTTP request 0.4 seconds apart over 40 seconds, a single 20 second outage
starting at request 25, `timeout: 5s`, the default in-memory queue with
`num_consumers: 10`, no batch processor and no tail sampling.

The span ordering is chosen to produce holes. Span `j` of trace `t` is request
`20j + t`, so every trace necessarily straddles the outage. A real service
usually finishes a trace in milliseconds, where the same outage would lose whole
traces and `Missing traces` would carry the damage. This run shows what
long-lived traces do, not what the average trace does.

`retry-10s` also sets `max_interval: 5s`, because the Collector refuses
`max_elapsed_time` below `max_interval`. Its numbers are a shorter budget and a
shorter backoff together, and this run cannot separate the two.

The split between delivered and dropped spans is not deterministic. `retry-off`
delivered 44 to 49 of 100 against 50 sent during the outage, because requests in
flight when the sink stopped, and the first requests after it started, land on
either side depending on timing within a 0.4 second step.

`queue_at_recovery` is the latest Prometheus sample immediately before restart,
so its resolution is the one-second scrape interval. It proves the observed
queue depth at that boundary, not which spans occupied those requests.

Log line counts are floors. The collector under test keeps its default sampler,
so `retry_log_lines` and `drop_log_lines` undercount events; `send_failed` and
the trace figures from the sink's unsampled log carry the result.

Nothing here tests a batch processor in front of the exporter, which would put
several spans of several traces in one request and change which spans share a
fate. Nothing tests a persistent queue, a Collector restart during the outage,
or a backend that rejects rather than refuses. The sink was always recovered, so
what happens after the retry budget runs out against a destination that never
returns is [L2-07](../L2-07-retry-exhausted/QUESTION.md).
