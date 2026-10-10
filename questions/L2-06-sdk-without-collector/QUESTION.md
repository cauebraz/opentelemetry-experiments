---
id: L2-06
title: What does the SDK do while there is no Collector to talk to?
level: 2
theme: sdk
status: measured
---

## Question

The application keeps running when the Collector does not. This question
measures what the Python SDK does with spans it cannot deliver: how long the
application thread waits, which spans survive an outage of a few seconds and
which do not, what a full queue drops first, and whether the application is
told.

## What the documentation says

On the batching processor:

> * `maxQueueSize` - the maximum queue size. After the size is reached, spans are
>   dropped. The default value is `2048`.
> * `scheduledDelayMillis` - the maximum delay interval in milliseconds between two
>   consecutive exports. The default value is `5000`.

On who retries:

> Concurrent requests and retry logic is the responsibility of the exporter. The
> default SDK's Span Processors SHOULD NOT implement retry logic, as the required
> logic is likely to depend heavily on the specific protocol and backend the spans
> are being sent to.

> * `Failure` - exporting failed. The batch must be dropped.

Source: [`specification/trace/sdk.md`, lines 1121-1124, 1171-1174 and 1193](https://github.com/open-telemetry/opentelemetry-specification/blob/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/trace/sdk.md?plain=1#L1121-L1193),
`opentelemetry-specification v1.61.0`, commit
`32c0b2c1d9e11f68da0e6e7b63044e57878590de`.

The specification does not say which spans are dropped when the queue is full,
nor how long the exporter retries. Both are in the Python implementation at
`opentelemetry-python v1.45.0`, commit
`4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2`, the version pinned in
`pyproject.toml`:

> ```python
> _MAX_RETRIES: Final[int] = 6
> _DEFAULT_TIMEOUT: Final[float] = 10.0
> ```

> ```python
>         return 2**retry * random.uniform(1 - self._jitter, 1 + self._jitter)
> ```

Source: [`exporter/opentelemetry-exporter-otlp-common/src/opentelemetry/exporter/otlp/common/http.py`, lines 30-31 and 140](https://github.com/open-telemetry/opentelemetry-python/blob/4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2/exporter/opentelemetry-exporter-otlp-common/src/opentelemetry/exporter/otlp/common/http.py#L30-L140).

> ```python
>                 # Oldest records are at the back, so pop from there.
> ```

> ```python
>             _logger.warning("Queue full, dropping %s.", self._exporting)
> ```

Source: [`opentelemetry-sdk/src/opentelemetry/sdk/_shared_internal/__init__.py`, lines 182 and 200](https://github.com/open-telemetry/opentelemetry-python/blob/4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2/opentelemetry-sdk/src/opentelemetry/sdk/_shared_internal/__init__.py#L182-L200).

All quotes checked against the raw files at those commits:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/trace/sdk.md | grep -n 'spans are$\|consecutive exports\|SHOULD NOT implement retry\|The batch must be dropped'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-python/4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2/exporter/opentelemetry-exporter-otlp-common/src/opentelemetry/exporter/otlp/common/http.py | grep -n '_MAX_RETRIES: Final\|_DEFAULT_TIMEOUT: Final\|return 2\*\*retry'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-python/4f0fcfa53e53a8c654a34df0d0a1fd74483ebca2/opentelemetry-sdk/src/opentelemetry/sdk/_shared_internal/__init__.py | grep -n 'Oldest records are at the back\|Queue full, dropping'
```

The code says the exporter retries with exponential backoff inside a 10 second
budget, and that the batch processor removes a batch from its queue before
exporting it. Whether a batch survives an outage therefore depends on the
outage ending inside that budget. What the application thread sees, and what
the queue holds when it overflows during a retry, is not written anywhere.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `collector-up` | batch processor at its defaults, 100 spans, all 100 at the sink, `span.end()` under 1 ms, no SDK warning |
| `simple-down` | `SimpleSpanProcessor`, 3 spans while the Collector is stopped and 3 after it is back; each of the first 3 `span.end()` calls blocks for about 7 seconds (backoff 1, 2 and 4 seconds, then 8 exceeds what is left of the 10 second budget) and the span is lost; the last 3 arrive and return in milliseconds; no exception reaches the application |
| `batch-short-outage` | batch processor at its defaults, 100 spans emitted while the Collector is stopped, Collector started immediately after; the first scheduled export runs after 5 seconds against a Collector that is back, so all 100 arrive with no SDK warning |
| `batch-long-outage` | 50 spans while the Collector is stopped for 20 seconds, then 50 after it is back; the first export at 5 seconds retries for about 7 seconds and gives up with one `Failed to export spans batch due to timeout, max retries or shutdown.` error; the first 50 are lost and the last 50 arrive; `span.end()` stays under 1 ms throughout |
| `small-queue` | `max_queue_size` and `max_export_batch_size` 50, 200 spans emitted at once while the Collector is stopped, Collector started immediately after; spans 0-49 leave the queue in the first batch and arrive on a retry, spans 50-149 are dropped as the queue overflows, spans 150-199 are what the queue holds and arrive after; 100 delivered as `0-49,150-199`, with a `Queue full, dropping span.` warning |

The prediction that decides how to read an SDK outage is `small-queue`. The
author expects the queue to keep the newest spans and drop the ones that
arrived while an export was retrying, because the deque discards from the old
end. If the delivered range is `0-99` instead, the queue keeps the oldest
spans and drops the newest.

The author expects the application never to be told. No exception reaches the
caller in any variant; the only signals are the SDK's own log records and, in
`simple-down`, the latency of `span.end()`.

## How to run

```bash
just ask L2-06
```

Five variants, three repetitions each. Each run starts the stack, then runs
`emitter.py` from this directory as a subprocess with the variant's name. The
emitter builds its own `TracerProvider` with the `opentelemetry-sdk` and
OTLP/HTTP exporter pinned in `pyproject.toml`, stops and starts the Collector
container itself at the points the variant names, and prints one JSON line with
what it observed. Every span is the root of its own trace, so a trace id
at the sink names exactly one span.

The Collector forwards traces to the sink with no batch processor. The sink
prints every span at `detailed` with log sampling disabled. The sink never
restarts, so its counter is cumulative over the run.

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.spans_emitted` | spans the application ended |
| `sending.end_ms_max_down`, `sending.end_ms_max_up` | slowest `span.end()` while the Collector was stopped and after it was back |
| `sending.exceptions` | exceptions that reached the application |
| `sending.collector_ready_seconds` | from starting the Collector to its health endpoint answering |
| `sending.sdk_warnings`, `sending.sdk_errors` | SDK log records at `warning` and `error` |
| `sending.export_failed_logs` | `Failed to export spans batch` records |
| `sending.queue_full_logs` | `Queue full, dropping span.` records |
| `counters.sink.traces.receiver_accepted` | spans that arrived |
| `measured.delivered` | distinct emitted spans found in the sink log |
| `measured.lost_down`, `measured.lost_up` | spans emitted during and after the outage that never arrived |
| `measured.delivered_ranges` | the indices that arrived, as ranges |

A run is invalid when the emitter exits non-zero or prints no result, when the
Collector does not become healthy after its restart, when the sink count
disagrees with the spans it printed, or when the sink printed a trace id the
emitter never produced.

Held constant: one Python process per run, `opentelemetry-sdk 1.45.0` and
`opentelemetry-exporter-otlp-proto-http 1.45.0`, the exporter's default 10
second timeout, OTLP/HTTP to `otelcol-contrib 0.162.0`. Only the processor, its
queue size and the outage timing vary. The SDK's batch logger carries a
duplicate filter, so log counts are floors; the sink figures carry the result.

## Result

15 runs, 0 invalid. The table is in
[`results/REPORT.md`](results/REPORT.md).

| Variant | Emitted while down | Slowest `end()` down | Slowest `end()` up | Delivered | Delivered ranges | SDK errors | Exceptions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `collector-up` | 0 | - | 0.01 ms | 100 | `0-99` | 0 | 0 |
| `simple-down` | 3 | 7.55 to 7.70 s | 1.95 to 3.04 ms | 3 | `3-5` | 3 | 0 |
| `batch-short-outage` | 100 | 0.04 to 0.06 ms | - | 100 | `0-99` | 0 | 0 |
| `batch-long-outage` | 50 | 0.03 to 0.06 ms | 0.04 to 0.05 ms | 50 | `50-99` | 1 | 0 |
| `small-queue` | 200 | 0.19 to 0.28 ms | - | 50 | `150-199` | 0 | 0 |

The prediction about the application held. No exception reached the caller in
any run. With the simple processor each `span.end()` during the outage blocked
the application thread for 7.55 to 7.70 seconds and the span was lost anyway,
one error log per span. With the batch processor `span.end()` stayed under 0.3
ms throughout.

The short and long outage predictions held. A Collector healthy again 0.42 to
0.43 seconds after its start cost nothing and logged nothing: the first export runs after 5 seconds. A 20
second outage lost the 50 spans due at the first export, after three retries
and one `Failed to export spans batch due to timeout, max retries or shutdown.`
error. That one line stands for 50 spans and carries no count.

The small-queue prediction failed in its detail. The author expected `0-49` to
leave in the first batch and arrive on a retry, plus `150-199` from the queue.
Only `150-199` arrived, in all three runs: the burst of 200 spans filled the
50-slot queue before the worker thread took a batch. The direction held: the
queue keeps the newest spans and drops the oldest, and it logged
`Queue full, dropping Span.` once for 150 drops.

What this does to an application: the batch processor is the one that keeps an
outage out of the request path, an outage longer than the exporter's 10 second
budget loses what was due, and `max_queue_size` has to fit the burst produced
while an export waits, because overflow drops the oldest spans with one log
line.

## What this does not say

Measured with `opentelemetry-sdk 1.45.0` and
`opentelemetry-exporter-otlp-proto-http 1.45.0` on Python 3.14, traces only, and
a Collector stopped with `docker compose stop`, so connections are refused at
once. A host that drops packets makes each attempt wait for its timeout and
spends the budget on fewer attempts.

Other language SDKs, the gRPC exporter, the log and metric pipelines, and
non-default `timeout` or `schedule_delay_millis` are not tested. The SDK's
internal metrics were not enabled, so this does not say whether they count the
drops that the log undercounts.

The small-queue result depends on a burst emitted faster than the worker thread
was scheduled. A slower producer would let the worker take a batch first and
change which spans survive.
