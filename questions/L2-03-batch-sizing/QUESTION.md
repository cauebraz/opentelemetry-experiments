---
id: L2-03
title: When does batch actually send, and what does each setting cost?
level: 2
theme: collector
status: measured
---

## Question

`send_batch_size` and `timeout` are two triggers for the same action. Across a
grid of both, this question measures how many sends were triggered by size and
how many by the timer, and what each combination does to end-to-end latency and
to request count.

The documentation names the two triggers and their defaults. It does not say
which of them fires in a given workload, nor whether the timer is measured from
the first item of a pending batch or from a ticker that runs for the lifetime of
the process. Those two readings of the same sentence predict different
latencies, and an operator choosing `timeout` is choosing one of them.

## What the documentation says

> - `send_batch_size` (default = 8192): Number of spans, metric data points, or log
> records after which a batch will be sent regardless of the timeout. `send_batch_size`
> acts as a trigger and does not affect the size of the batch. If you need to
> enforce batch size limits sent to the next component in the pipeline
> see `send_batch_max_size`.
> - `timeout` (default = 200ms): Time duration after which a batch will
> be sent regardless of size.  If set to zero, `send_batch_size` is
> ignored as data will be sent immediately, subject to only `send_batch_max_size`.

Source: [`processor/batchprocessor/README.md`, lines 27-34](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/processor/batchprocessor/README.md?plain=1#L27-L34),
`opentelemetry-collector v0.162.0`, commit
`62cdad2ea133239380b44d20d84eb26e114779b6`.

Checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/processor/batchprocessor/README.md | grep -n 'send_batch_size (default\|timeout (default\|acts as a trigger'
```

Two sentences carry a testable claim. "Number of spans, metric data points, or
log records after which a batch will be sent regardless of the timeout" is a
count of items, not of requests, so 100 requests of one datapoint each should
behave like one request of 100 datapoints. "`send_batch_size` acts as a trigger
and does not affect the size of the batch" says the batch may exceed the
trigger, which `send_batch_max_size` at its default of 0 does not cap.

The page is silent on the clock. "Time duration after which a batch will be
sent" does not say after which event the duration is counted.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `no-batch` | 100 requests at the sink, no `otelcol_processor_batch_*` series at all |
| `defaults` | about 25 timer-triggered sends of about 4 datapoints, 0 size triggers, complete in 5 to 6 seconds |
| `size-1` | 100 size-triggered sends of 1 datapoint, 0 timer triggers, 100 requests at the sink |
| `size-5-timeout-10s` | 20 size-triggered sends of 5 datapoints, 0 timer triggers, complete in 5 to 6 seconds |
| `size-8192-timeout-10s` | 1 timer-triggered send of 100 datapoints, complete in 10 to 11 seconds |

The prediction that decides how `timeout` should be read is the last row. The
author expects the timer to start when the first item of a pending batch
arrives, so one flush 10 seconds after the first request, repeatable across
the three repetitions. If instead the three repetitions disagree and land
anywhere between 5 and 10 seconds, the timer is a ticker running on the
process clock and `timeout` is an upper bound on added latency rather than the
latency itself.

The author also expects `batch_size_trigger_send` plus `timeout_trigger_send`
to equal `batch_send_size_count` in every variant, and the sum of
`batch_send_size` to equal 100 datapoints everywhere, because the trigger
chooses when to send and not what to drop.

Cost: the author expects bytes and request count to fall together, so
`size-8192-timeout-10s` should move the same 100 datapoints in one request and
fewer bytes than `no-batch` moves in a hundred.

## How to run

```bash
just ask L2-03
```

Five variants, three repetitions each. Each run posts 100 OTLP/HTTP requests
carrying one metric datapoint each, 50ms apart, then polls the sink until all
100 datapoints have arrived and the count stops rising. The three repetitions
of `size-8192-timeout-10s` wait 0, 3 and 7 seconds after the Collector is ready
and before starting the measured interval. If the timeout is a process-relative
ticker, those phases move the first flush; if it starts with the first pending
item, elapsed time from the first request stays at ten seconds.

Without the harness:

```bash
COLLECTOR_CONFIG=./questions/L2-03-batch-sizing/config/collector-size-5-timeout-10s.yaml just up
for i in $(seq 1 100); do \
  curl -s -o /dev/null -X POST http://127.0.0.1:24318/v1/metrics \
    -H 'Content-Type: application/json' \
    -d '{"resourceMetrics":[{"scopeMetrics":[{"metrics":[{"name":"l2_03.requests","unit":"1","sum":{"dataPoints":[{"asDouble":1,"timeUnixNano":"1760000000000000000"}],"isMonotonic":true,"aggregationTemporality":"AGGREGATION_TEMPORALITY_DELTA"}}]}]}]}'; \
  sleep 0.05; \
done
curl -s http://127.0.0.1:28888/metrics | grep trigger_send
just logs sink | grep -c 'data points'
```

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the highest status returned across the 100 requests |
| `sending.requests_sent` | requests posted, held at 100 |
| `sending.pre_send_idle` | seconds between readiness and the first request; 0, 3 and 7 for the long-timeout repetitions |
| `sending.seconds_to_complete` | monotonic seconds from the first request until the sink's accepted count reached 100 and stopped rising |
| `counters.collector.metrics.receiver_accepted` | datapoints accepted before the processor |
| `measured.size_triggers` | `otelcol_processor_batch_batch_size_trigger_send` |
| `measured.timeout_triggers` | `otelcol_processor_batch_timeout_trigger_send` |
| `measured.batches_sent` | `otelcol_processor_batch_batch_send_size_count` |
| `measured.mean_batch_size` | `batch_send_size_sum` divided by `batch_send_size_count` |
| `measured.bytes_batched` | `otelcol_processor_batch_batch_send_size_bytes_sum`, published only at telemetry level `detailed` |
| `measured.requests_at_sink` | summary lines the sink's `debug` exporter wrote, one per export request; the sink runs with log sampling off, so this is a count and not a floor |
| `measured.largest_request_at_sink` | the largest datapoint count among those requests |
| `measured.sent_to_sink` | successful datapoints attributed to the `otlp_grpc/sink` exporter |
| `counters.sink.metrics.receiver_accepted` | datapoints the sink accepted |

Held constant: 100 requests of one datapoint each 50ms apart, an OTLP gRPC
destination with the default queue and retry, `send_batch_max_size` at its
default of 0, telemetry level `detailed`, and `otelcol-contrib 0.162.0`. Only
the batch configuration varies.

A counter the Collector has never incremented publishes no series. Every
`measured` cell reading `-` is therefore an absent series, not a measured zero,
and the table says which.

The one-second Prometheus scrape bounds `seconds_to_complete`: it is accurate
to a second, whatever the 200ms poll suggests.

## Result

15 runs, 0 invalid. The table is in [`results/REPORT.md`](results/REPORT.md).

| Variant | Idle | Size triggers | Timer triggers | Batches | Mean size | Bytes | Requests at the sink | Seconds to complete |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `no-batch` | 0 | - | - | - | - | - | 100 | 6 |
| `defaults` | 0 | - | 28 | 28 | 3.6 | 14500 | 28 | 7 (6 to 7) |
| `size-1` | 0 | 100 | - | 100 | 1.0 | 14500 | 100 | 7 (6 to 7) |
| `size-5-timeout-10s` | 0 | 20 | - | 20 | 5.0 | 14500 | 20 | 6 |
| `size-8192-timeout-10s` | 3 (0 to 7) | - | 1 (1 to 2) | 1 (1 to 2) | 100.0 (50.0 to 100.0) | 14500 | 1 (1 to 2) | 11 (8 to 14) |

Three of the five predictions held. In every variant exactly one trigger fired
and the other published no series, `size_triggers` plus `timeout_triggers`
equals `batches_sent`, and `batches_sent` equals the export requests the sink
logged. The sender finished in 5.6 seconds in all fifteen runs.

The central prediction failed. After 0, 3 and 7 seconds idle,
`size-8192-timeout-10s` completed 11, 8 and 14 seconds after its first request.
The first two phases sent all 100 datapoints on one process-relative tick. With
7 seconds idle, the first tick interrupted the 5.6 second send and a second
tick was needed, producing two requests of 50 datapoints on average. `timeout`
is an upper bound on waiting for a process-relative tick, not a delay counted
from the first pending item.

`defaults` sent 28 or 29 times rather than the predicted 25, which is 5.6
seconds of sending divided by the 200ms timeout rather than the 5.0 seconds the
prediction assumed. The mean batch held 3.4 to 3.6 datapoints.

The cost prediction failed. `batch_send_size_bytes_sum` reads 14500 in every
variant that has a batch processor, at 1 datapoint per batch and at 100 alike:
145 bytes per datapoint, summed before the items become a request. The
histogram does not see the saving that batching exists for, and `no-batch`
publishes no bytes series at all. Request count is the figure that moved, from
100 down to 1.

What this does to a dashboard: read the two trigger counters together, because
the quiet one is absent rather than zero. A panel on `timeout_trigger_send`
alone is blank while `send_batch_size` is in control, and it is the only
warning that `timeout` is adding ten seconds of latency when it is.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one metrics pipeline, 100 OTLP/HTTP
requests of one datapoint each 50ms apart, an OTLP gRPC destination with the
default queue and retry, and telemetry level `detailed`. A different arrival
rate moves every number in the table.

The claim that `send_batch_size` "acts as a trigger and does not affect the size
of the batch" was not exercised. Items arrived one at a time, so no batch could
overshoot its trigger, and `largest_request` never exceeded `send_batch_size`.
Whether a single request of 1000 datapoints is split needs requests larger than
one item and a `send_batch_max_size` variant.

`seconds_to_complete` is accurate to one second, because Prometheus scrapes the
sink every second. It measures when the datapoints were counted at the sink, not
when the Collector called the exporter. The 8 to 14 second range and the second
trigger after 7 seconds idle distinguish a process-relative ticker from a timer
started by the first item; they do not locate that ticker in the Collector's
code.

The bytes figure is the batch processor's own histogram. It is not the size of
the gRPC request, not compressed, and not what a network bill would count. No
compression variant was run.

Nothing failed in these runs. Every variant delivered 100 datapoints with HTTP
200 and no retry, so this says nothing about how a batch behaves when its export
fails, which is [L2-05](../L2-05-traces-under-exporter-failure/QUESTION.md), or
about `send_batch_max_size`, `metadata_keys` and the exporter's own batching,
none of which were configured.
