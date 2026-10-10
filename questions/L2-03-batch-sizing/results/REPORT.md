# L2-03 When does batch actually send, and what does each setting cost?

Level 2, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L2-03`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `no-batch` | no batch processor in the pipeline |
| `defaults` | batch at its defaults, 8192 datapoints or 200ms |
| `size-1` | send_batch_size 1, timeout 200ms |
| `size-5-timeout-10s` | send_batch_size 5, timeout 10s |
| `size-8192-timeout-10s` | send_batch_size 8192, timeout 10s |

## Measured

| Variant | HTTP status | Requests sent | Pre-send idle | Accepted | Size triggers | Timer triggers | Batches sent | Mean batch size | Bytes batched | Requests at the sink | Largest request | Sent to the sink | Arrived at the sink | Seconds to complete |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `no-batch` | 200 | 100 | 0 | 100 | - | - | - | - | - | 100 | 1 | 100 | 100 | 6 |
| `defaults` | 200 | 100 | 0 | 100 | - | 28 | 28 | 3.6 | 14500 | 28 | 4 | 100 | 100 | 7 (6 to 7) |
| `size-1` | 200 | 100 | 0 | 100 | 100 | - | 100 | 1.0 | 14500 | 100 | 1 | 100 | 100 | 7 (6 to 7) |
| `size-5-timeout-10s` | 200 | 100 | 0 | 100 | 20 | - | 20 | 5.0 | 14500 | 20 | 5 | 100 | 100 | 6 |
| `size-8192-timeout-10s` | 200 | 100 | 3 (0 to 7) | 100 | - | 1 (1 to 2) | 1 (1 to 2) | 100.0 (50.0 to 100.0) | 14500 | 1 (1 to 2) | 100 (50 to 100) | 100 | 100 | 11 (8 to 14) |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The two triggers never competed. In every variant one of them fired and the
other published no series at all, and the one that fired accounts for every
batch: `size_triggers` plus `timeout_triggers` equals `batches_sent` in all
five variants, and `batches_sent` equals the number of export requests the sink
logged.

Which one fires is decided by the arrival rate, not by the configuration alone.
The same 100 datapoints, arriving one per request 50ms apart over 5.6 seconds,
produce 100 size-triggered sends at `send_batch_size: 1`, 20 at
`send_batch_size: 5`, and 0 at `send_batch_size: 8192`, where the timer sends
instead. At the defaults the timer sends 28 or 29 times, which is 5.6 seconds
divided by the 200ms timeout, and the mean batch holds 3.4 to 3.6 datapoints.
A `send_batch_size` above the arrival rate times the timeout is not a batch
size, it is an unused branch.

The timer is process-relative, so the central prediction failed. The
`size-8192-timeout-10s` repetitions waited 0, 3 and 7 seconds after readiness
before the first request, then completed 11, 8 and 14 seconds after that
request. With 0 seconds idle, one tick sent all 100 datapoints. With 3 seconds
idle, that tick arrived about 7 seconds after the first item and still sent all
100. With 7 seconds idle, the first tick interrupted the 5.6 second send and a
second tick was needed, so the run emitted two requests of 50 datapoints on
average.

`timeout` is therefore an upper bound on how long a pending item waits for a
process-relative tick, not a delay counted from the first item. The phase can
also split one workload into two export requests without any change in arrival
rate or batch settings.

Request count follows the trigger exactly. `no-batch` put 100 requests on the
wire for 100 datapoints, `size-5-timeout-10s` put 20, and
`size-8192-timeout-10s` put 1. That is the number that costs money on a billed
endpoint and connections on a shared one, and the sink's log counts it directly
because the sink runs with log sampling off.

The cost prediction failed, and it failed in a way worth knowing.
`otelcol_processor_batch_batch_send_size_bytes_sum` reads 14500 in every
variant that has a batch processor: at 1 datapoint per batch and at 100, the
same 100 datapoints, the same 145 bytes each. The histogram sums the bytes of
the items, before they become a request, so it does not see the saving that
batching exists for. A dashboard built on it shows batching as free and as
useless at the same time. `no-batch` publishes no bytes series, so removing the
processor also removes the only byte figure the pipeline reported.

What this does to a dashboard: read the two trigger counters together, because
the quiet one is quiet by being absent, not by reading zero. A panel on
`timeout_trigger_send` alone shows nothing at all when `send_batch_size` is
doing the work. When the timer is in control, its counter says how many process
ticks found data, while end-to-end latency still depends on the workload's phase
against those ticks.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one metrics pipeline, 100 OTLP/HTTP
requests of one datapoint each 50ms apart, an OTLP gRPC destination with the
default queue and retry, and telemetry level `detailed`. A different arrival
rate moves every number in the table: the trigger that fires is a function of
the rate against the configuration, and this run holds the rate constant.

The claim that `send_batch_size` "acts as a trigger and does not affect the
size of the batch" was not exercised. Items arrived one at a time, so no batch
could overshoot its trigger, and `largest_request` never exceeded
`send_batch_size`. Whether a single request of 1000 datapoints is split, or
sent whole past a `send_batch_size` of 5, needs requests larger than one item
and a `send_batch_max_size` variant.

`seconds_to_complete` is accurate to one second, because Prometheus scrapes the
sink every second. It measures when the datapoints were counted at the sink,
not when the Collector called the exporter. The 8 to 14 second range and the
second trigger after 7 seconds idle distinguish a process-relative ticker from
a timer started by the first item; they do not locate that ticker in the
Collector's code.

The bytes figure is the batch processor's own histogram at telemetry level
`detailed`. It is not the size of the gRPC request, not compressed, and not
what a network bill would count. No compression variant was run, and the wire
size was never measured here.

Nothing failed in these runs. Every variant delivered 100 datapoints with HTTP
200 and no retry, so this says nothing about how a batch behaves when its
export fails, which is L2-05, or about `send_batch_max_size`, `metadata_keys`
and the exporter's own batching, none of which were configured.
