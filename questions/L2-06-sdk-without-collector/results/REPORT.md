# L2-06 What does the SDK do while there is no Collector to talk to?

Level 2, theme sdk. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L2-06`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `collector-up` | batch processor at its defaults, Collector running |
| `simple-down` | SimpleSpanProcessor, 3 spans with the Collector stopped and 3 after |
| `batch-short-outage` | batch defaults, 100 spans while stopped, Collector started at once |
| `batch-long-outage` | batch defaults, 50 spans before a 20s outage and 50 after it |
| `small-queue` | queue and batch size 50, 200 spans while stopped, started at once |

## Measured

| Variant | Spans emitted | Emitted while down | Slowest end() down ms | Slowest end() up ms | Exceptions | Collector ready s | Sink accepted | Delivered | Lost while down | Lost after | Delivered ranges | SDK warnings | SDK errors | Export failed logs | Queue full logs | Emitter seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `collector-up` | 100 | 0 | - | 0.01 | 0 | - | 100 | 100 | 0 | 0 | 0-99 | 0 | 0 | 0 | 0 | 0.0 |
| `simple-down` | 6 | 3 | 7663.45 (7554.09 to 7696.05) | 2.28 (1.95 to 3.04) | 0 | 0.44 (0.43 to 0.55) | 3 | 3 | 3 | 0 | 3-5 | 9 | 3 | 3 | 0 | 22.0 (21.5 to 22.7) |
| `batch-short-outage` | 100 | 100 | 0.04 (0.04 to 0.06) | - | 0 | 0.42 (0.42 to 0.43) | 100 | 100 | 0 | 0 | 0-99 | 0 | 0 | 0 | 0 | 10.8 (10.8 to 10.9) |
| `batch-long-outage` | 100 | 50 | 0.03 (0.03 to 0.06) | 0.04 (0.04 to 0.05) | 0 | 0.44 (0.41 to 0.45) | 50 | 50 | 50 | 0 | 50-99 | 3 | 1 | 1 | 0 | 20.9 (20.8 to 20.9) |
| `small-queue` | 200 | 200 | 0.19 (0.19 to 0.28) | - | 0 | 0.41 (0.41 to 0.42) | 50 | 50 | 150 | 0 | 150-199 | 2 | 0 | 0 | 1 | 10.8 (10.8 to 10.9) |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The application was never told. No exception reached the caller in any of the
15 runs, including the ones that lost every span emitted during the outage.
What changed was the SDK's own log, and, with the simple processor, how long the
application thread waited.

`SimpleSpanProcessor` moved the outage into the request path. Each
`span.end()` while the Collector was stopped blocked for 7.55 to 7.70 seconds,
the exporter's backoff of about 1, 2 and 4 seconds before the next 8 second step
no longer fit in its 10 second budget. The span was lost anyway, with one
`Failed to export spans batch due to timeout, max retries or shutdown.` error
per span. After the Collector was back, `span.end()` took 1.95 to 3.04 ms and
all 3 spans arrived.

The batch processor kept `span.end()` under 0.3 ms in every run, and survived a
short outage completely. With the Collector started right after the spans and
healthy 0.42 to 0.43 seconds later, the first scheduled export ran after the 5
second delay against a Collector that was back: 100 of 100 arrived, with no SDK
warning at all. The outage left no trace anywhere.

A 20 second outage lost the batch that was due inside it. The export at 5
seconds retried three times and gave up with one error, and spans 0-49 never
arrived; the 50 spans emitted after recovery all did. One error line stands for
50 lost spans, and it carries no count.

The small queue failed the author's prediction in its detail and confirmed it in
its direction. Only spans 150-199 arrived, in all three runs. The author
expected 0-49 to leave in a first batch and survive on a retry, but the burst of
200 spans filled the 50-slot queue before the worker thread took a batch. The
queue kept the newest 50 and discarded the 150 older ones, and the SDK logged
`Queue full, dropping Span.` once for 150 drops, because its batch logger
filters duplicates.

What this does to an application: use the batch processor, accept that an
outage longer than the exporter's budget loses what was due, and size
`max_queue_size` for the burst the process can produce while an export waits,
because overflow drops the oldest spans silently.

## What this does not say

Measured with `opentelemetry-sdk 1.45.0` and
`opentelemetry-exporter-otlp-proto-http 1.45.0` on Python 3.14, traces only, a
Collector container stopped with `docker compose stop` so connections are
refused at once. An unreachable host that drops packets makes each attempt wait
for its timeout instead, and spends the 10 second budget on fewer attempts.

Other language SDKs, the gRPC exporter, the log and metric pipelines of this SDK,
and the exporter's `timeout` and the processor's `schedule_delay_millis` at
anything but their defaults are not tested. The SDK's internal metrics were not
enabled, so this does not say whether they count the drops the log
undercounts.

The small-queue result depends on a burst emitted faster than the worker thread
was scheduled. A slower producer would let the worker take a batch first, and
which spans survive would change.
