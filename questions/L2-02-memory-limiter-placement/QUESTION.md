---
id: L2-02
title: Does memory_limiter belong before or after batch?
level: 2
theme: collector
status: measured
---

## Question

The processor refuses data when the Collector is near its memory limit. Its
position in the list decides whether the refusal lands on whole batches or on
individual records, and whether the sender learns about it. This question runs
both orders under the same pressure.

The pressure is the one an outage produces. The destination is stopped, the
exporter queue keeps every request it cannot deliver, and the heap grows until
the limiter refuses. The question is what each order does with the next
request, and what the sender, the counters and the log can tell afterwards.

## What the documentation says

On how the processor refuses:

> The processor will enter memory limited mode and will start refusing the data when
> memory usage exceeds the soft limit. This is done by returning errors to the preceding component
> in the pipeline that made the ConsumeLogs/Trace/Metrics function call.

> Warning: Data will be permanently lost if the component preceding the memory limiter
> in the telemetry pipeline does not correctly retry sending data after it has
> been refused by the memory limiter.
> We consider such components to be incorrectly implemented.

On where it goes:

> For the `memory_limiter` processor, the
> best practice is to add it as the first processor in a pipeline. This is to ensure that backpressure
> can be sent to applicable receivers and minimize the likelihood of dropped data when the
> `memory_limiter` gets triggered.

Source: [`processor/memorylimiterprocessor/README.md`, lines 36-38, 53-56 and 82-85](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/processor/memorylimiterprocessor/README.md?plain=1#L36-L85),
`opentelemetry-collector v0.162.0`, commit
`62cdad2ea133239380b44d20d84eb26e114779b6`.

And from the batch processor:

> The batch processor should be defined in the pipeline after the `memory_limiter`
> as well as any sampling processors. This is because batching should happen after
> any data drops such as sampling.

Source: [`processor/batchprocessor/README.md`, lines 20-22](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/processor/batchprocessor/README.md?plain=1#L20-L22),
same commit.

All quotes checked against the raw files at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/processor/memorylimiterprocessor/README.md | grep -n 'preceding component\|permanently lost\|first processor in a pipeline'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/processor/batchprocessor/README.md | grep -n 'after the `memory_limiter`'
```

The pages say the limiter returns an error to whatever precedes it, and that
the data is lost if that component does not retry. They do not say what the
batch processor does with that error, which is the component that precedes the
limiter in the other order. The batch README gives a reason for its own
position, but the reason is sampling, not memory. Neither page says what the
sender sees.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `control` | 20 requests of 10 records, all HTTP 200, no refusal, peak heap under the 72 MiB soft limit, all 200 records at the sink after recovery |
| `limiter-first` | HTTP 200 until the heap crosses the soft limit, then a retryable non-200 status (503 or 429) on the next request; every record of every 200 request reaches the sink after recovery; the records lost are exactly the ones in the refused request, and the sender knows which they are |
| `batch-first` | HTTP 200 for every one of the 200 requests; `otelcol_receiver_refused_log_records` stays 0; the limiter refuses whole batches from the batch processor, which logs `Sender failed` and drops them; hundreds of records answered with 200 never reach the sink |

The prediction that decides the placement is the last row. The author expects
the batch processor to drop what the limiter refuses, because it already
answered the receiver and holds no copy to retry. If instead `batch-first`
delivers every record acknowledged with 200, the order only changes where the
refusal is counted, not whether data is lost.

The author also expects `otelcol_processor_memory_limiter_refused_log_records`
to count the refused records in both pressure variants, so the loss is visible
in a counter even in the order where the sender never learns of it, and expects
the peak heap to stay below the 96 MiB hard limit in both, so the container
ceiling of 512m is never reached.

## How to run

```bash
just ask L2-02
```

Three variants, three repetitions each. Each run starts the stack, stops the
sink, and sends OTLP/HTTP log requests of 10 records 50ms apart. Each record
carries a sequence id as its body and a 64 KiB `padding` attribute. The sender
stops at the first non-200 status or after 200 requests, whichever is first;
the control stops after 20. Five seconds later the sink is started again, and
the run waits for the exporter queue to reach 0 and the sink count to stop
changing.

The sink's own config deletes `padding` before its `debug` exporter prints each
record at `detailed`, so the sink log carries every sequence id that arrived
without carrying 64 KiB per record.

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.requests_sent`, `sending.records_sent` | what the sender put on the wire before it stopped |
| `sending.status_counts` | how many requests got each HTTP status |
| `sending.first_refused_request` | index of the first non-200 request, `None` when every request got 200 |
| `sending.latency_ms_p50`, `sending.latency_ms_max` | request round trip as the sender saw it |
| `sending.heap_peak_mib` | `max_over_time` of `otelcol_process_runtime_heap_alloc_bytes` over the run |
| `sending.rss_peak_mib` | `max_over_time` of `otelcol_process_memory_rss` over the run |
| `sending.limiter_refused` | `otelcol_processor_memory_limiter_refused_log_records` |
| `sending.queue_size_max` | `max_over_time` of `otelcol_exporter_queue_size` |
| `sending.oom_killed`, `sending.restarts` | the collector container state before teardown |
| `sending.drain_seconds` | from starting the sink to the last change in the sink count |
| `counters.collector.logs.receiver_accepted`, `receiver_refused` | the OTLP receiver's own counters |
| `counters.sink.logs.receiver_accepted` | records that arrived |
| `measured.delivered_unique` | distinct sequence ids in the sink log |
| `measured.duplicates` | records printed more than once |
| `measured.acked_not_delivered` | records in requests answered 200 that never arrived |
| `measured.refused_records` | records in requests answered with a non-200 status |
| `measured.refusing_lines` | collector log lines `Memory usage is above soft limit. Refusing data.` |
| `measured.sender_failed_lines` | collector log lines `Sender failed` from the batch processor |

A run is invalid when the collector was OOM killed or restarted, when the sink
count disagrees with the records printed in its unsampled log, when the sink
printed a sequence id that was never sent, when a pressure variant never
engaged the limiter, or when the control did.

Held constant: `memory_limiter` with `check_interval: 1s`, `limit_mib: 96` and
`spike_limit_mib: 24` (soft limit 72 MiB), the `batch` processor at its
defaults, the default exporter queue and retry with `timeout: 5s`, a 512m
container ceiling, and `otelcol-contrib 0.162.0`. Only the processor order and
the amount of load vary. Heap and RSS peaks come from a 1 second scrape, so a
peak shorter than a second can be missed.

## Result

9 runs, 0 invalid. The table is in
[`results/REPORT.md`](results/REPORT.md).

| Variant | Statuses | Accepted | Receiver refused | Limiter refused | Delivered | Acked, not delivered | Heap peak MiB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `control` | 20 x 200 | 200 | 0 | - | 200 | 0 | 38.4 (37.9 to 39.1) |
| `limiter-first` | 55 to 57 x 200, then one 503 | 560 (550 to 570) | 10 | 10 | 560 (550 to 570) | 0 | 89.8 (84.9 to 90.2) |
| `batch-first` | 200 x 200 | 2000 | 0 | 1140 (1130 to 1140) | 860 (860 to 870) | 1140 (1130 to 1140) | 108.1 (104.9 to 111.9) |

The deciding prediction held. Behind batch, the limiter's refusals became loss
the sender never heard about: every request got HTTP 200 and 1130 to 1140
records did not arrive. The batch processor logged the refusal and did not
retry:

```text
warn  batchprocessor@v0.162.0/batch_processor.go:272  Sender failed
"error": "data refused due to high memory usage"
```

In front of batch, the limiter refused the next request with HTTP 503, not 429,
and every record acknowledged with 200 arrived after recovery. The loss is the
refused request and the sender knows it.

The counter prediction held. `otelcol_processor_memory_limiter_refused_log_records`
equals the acknowledged-but-lost records in each `batch-first` run, and the 10
refused records under `limiter-first`. It is the only signal that carries the
size of the loss: the `Sender failed` line appears exactly 10 times per run, the
sampler's first-ten allowance, and has no item count.

The heap prediction failed. `batch-first` peaked at 104.9 to 111.9 MiB, above the
96 MiB hard limit, against 84.9 to 90.2 MiB for `limiter-first`. The batch
processor accepts and holds data before the limiter sees it, so the limiter
refuses later. The container ceiling was not reached: RSS peaked at 293.3 MiB
of 512m and no run was OOM killed.

What this does to a pipeline: `memory_limiter` goes first, which is what the
README recommends; this run gives the reason in records. Where anything sits in
front of it, `otelcol_processor_memory_limiter_refused_*` is a data-loss
counter, not a back-pressure counter.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with logs only, one OTLP/HTTP receiver,
`limit_mib: 96`, `spike_limit_mib: 24`, the batch processor at its defaults and
a sink stopped for the whole sending phase. Pressure comes from the exporter
queue holding undeliverable data; a burst against a healthy destination is a
different shape of pressure.

The sender stops at the first non-200 status and does not retry. A client that
retries 503 would turn the 10 refused records into delayed delivery, but that
client was not run.

The OTLP/gRPC receiver, traces and metrics, other processors in front of the
limiter, and a persistent queue are not tested. Heap and RSS peaks come from a
one second scrape and can miss a shorter peak. Log line counts are floors under
the Collector's default sampler.
