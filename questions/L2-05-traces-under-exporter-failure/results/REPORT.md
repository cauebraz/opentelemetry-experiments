# L2-05 What does a trace look like when the exporter failed halfway through it?

Level 2, theme traces. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L2-05`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `sink-up` | the sink reachable throughout, default retry |
| `retry-default` | the sink stopped for 20s of the 40s, retry at its defaults |
| `retry-off` | the sink stopped for 20s of the 40s, retry_on_failure disabled |
| `retry-10s` | the sink stopped for 20s of the 40s, max_elapsed_time 10s |

## Measured

| Variant | HTTP status | Spans sent | Accepted | Sent to the sink | Send failed | Enqueue failed | Queue size max | Queue at recovery | Spans arrived | Sink accepted | Complete traces | Incomplete traces | Missing traces | Trace size distribution | Roots arrived | Largest hole | Seconds to drain | Requests at the sink | Retry lines | Drop lines | Sink target up | Error lines | Recovered |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sink-up` | 200 | 100 | 100 | 100 | - | - | 0 | - | 100 | 100 | 20 | 0 | 0 | 5:20 | 20 | 0 | - | 100 | 0 | 0 | 1 | 0 | not applicable |
| `retry-default` | 200 | 100 | 100 | 100 | - | - | 61 (55 to 61) | 49 (48 to 50) | 100 | 100 | 20 | 0 | 0 | 5:20 | 20 | 0 | 23.4 (20.4 to 26.4) | 100 | 21 (21 to 23) | 0 | 1 | 0 | yes |
| `retry-off` | 200 | 100 | 100 | 48 (44 to 49) | 52 (51 to 56) | - | 0 | 0 | 48 (44 to 49) | 48 (44 to 49) | 0 | 20 | 0 | varies: 2:12; 3:8, 2:11; 3:9, 2:16; 3:4 | 20 | 3 | 10.3 | 48 (44 to 49) | 0 | 22 (21 to 26) | 1 | 22 (21 to 26) | yes |
| `retry-10s` | 200 | 100 | 100 | 80 (78 to 80) | 20 (20 to 22) | - | 32 (30 to 33) | 30 (30 to 32) | 80 (78 to 80) | 80 (78 to 80) | 0 (0 to 1) | 20 (19 to 20) | 0 | varies: 3:2; 4:18, 3:1; 4:18; 5:1, 4:20 | 20 | 2 (1 to 2) | 10.3 (10.3 to 11.3) | 80 (78 to 80) | 23 (20 to 25) | 16 (14 to 20) | 1 | 16 (14 to 20) | yes |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

With retry disabled, a 20 second outage did not lose traces. It corrupted all
of them. Every one of the twenty traces arrived at the sink, and not one of them
was complete. Across the three repetitions, 11 to 16 traces held 2 of their 5
spans and 4 to 9 held 3. `trace_size_counts` now carries that distribution in
`results.json`, instead of leaving it to a manual reading of the sink log.

All twenty root spans arrived in every variant, including the two lossy ones.
The root was sent before the outage began, so all twenty trace ids exist at the
destination while the missing children are visible only as an absence.

The default retry absorbed the outage completely. `retry-default` delivered 100
spans and 20 complete traces in all three repetitions, with 0 error lines and 0
drop lines. The queue held 48 to 50 requests immediately before recovery,
peaked at 55 to 61, and the last sink-counter change came 20.4 to 26.4 seconds
after restart. The cumulative sink counter and the unsampled sink log both read
100, which verifies the two evidence paths after accounting for the counter
reset at restart.

Shortening the budget recovered 78 to 80 spans, but only 0 or 1 complete trace.
At recovery the queue gauge read 30 to 32 requests, the final loss was 20 to 22
spans, and the last sink-counter change came 10.3 to 11.3 seconds after restart.
Those observations are consistent with queued requests surviving long enough
to be retried, but this run does not correlate a queue slot with a span and does
not establish which queue positions survived.

The counters measured here do not know what a trace is.
`otelcol_exporter_send_failed_spans` counted 51 to 56 spans under `retry-off`,
which cannot distinguish 20 damaged traces from a smaller number of missing
ones. `queue_size` counts requests. `enqueue_failed` was never published,
because the queue peaked at 61 of its 1000 request capacity.

The early signal from L2-01 disappears in the worst case. `queue_size_max` reads
0 under `retry-off`: with retry disabled the request fails on its first attempt
and never dwells in the queue, so the gauge stays flat while 51 to 56 spans are
discarded. Queue depth warns about delivery that is waiting, not about delivery
already abandoned.

What remains is the log, and it is sampled. Each dropped request produced
`Exporting failed. Dropping data. Try enabling retry_on_failure config option to
retry on retryable errors.` at `error` with `"dropped_items": 1`, one span per
line. `retry-off` recorded 21 to 26 of those lines for 51 to 56 dropped spans,
so the text undercounts under the Collector's default sampler. The counter is
the number to alert on; the log line is where the reason is.

What this does to a dashboard: alert on `send_failed_spans` for the loss and on
destination availability for the cause, and accept that neither says a trace is
incomplete. Detecting that needs the backend to compare spans against their
parents, because the Collector never had the whole trace in one place.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with 20 traces of 5 spans, one span per
OTLP/HTTP request 0.4 seconds apart over 40 seconds, a single 20 second outage
starting at request 25, `timeout: 5s`, the default in-memory queue with
`num_consumers: 10`, no batch processor, and no tail sampling. Only
`retry_on_failure` varies.

The span ordering is chosen to produce holes. Span `j` of trace `t` is request
`20j + t`, so every trace necessarily straddles the outage. A real service
usually finishes a trace in milliseconds, where the same outage would lose whole
traces instead and `Missing traces` would carry the damage. This run shows what
long-lived traces do, not what the average trace does.

`retry-10s` also sets `max_interval: 5s`, because the Collector refuses
`max_elapsed_time` below `max_interval` and the default ceiling is 30 seconds.
Its numbers are therefore a shorter budget and a shorter backoff together, and
this run cannot separate the two.

The split between delivered and dropped spans is not deterministic.
`retry-off` delivered 44 to 49 of 100 against 50 sent during the outage,
because requests in flight when the sink stopped, and the first requests after
it started, land on either side depending on timing within a 0.4 second step.

`queue_at_recovery` is the latest Prometheus sample immediately before restart,
so its resolution is the one-second scrape interval. It proves the observed
queue depth at that boundary, not which spans occupied those requests.

Log line counts are floors. The Collector keeps its default sampler, so
`retry_log_lines` and `drop_log_lines` undercount events; `send_failed` and the
trace figures from the sink's unsampled log carry the result.

Nothing here tests a batch processor in front of the exporter, which would put
several spans of several traces in one request and change which spans share a
fate. Nothing tests a persistent queue, a Collector restart during the outage,
or a backend that rejects rather than refuses. The sink was always recovered,
so the behaviour after the retry budget is exhausted against a destination that
never returns is L2-07, not this.
