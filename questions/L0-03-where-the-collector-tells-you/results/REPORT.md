# L0-03 Which surface tells you what the Collector actually did?

Level 0, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L0-03`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `healthy` | every surface available, nothing failing |
| `telemetry-basic` | service.telemetry.metrics.level set to basic |
| `telemetry-none` | service.telemetry.metrics.level set to none |
| `no-debug-exporter` | the same pipeline with the debug exporter removed |
| `sink-stopped` | the downstream collector stopped just before the burst |

## Measured

| Variant | Sent by us | HTTP status | Accepted | Refused | Sent, all exporters | Send failed | Sent to the sink | Arrived at the sink | Printed by debug | Exporters counted | Health status | Warn lines | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `healthy` | 100 | 200 | 100 | 0 | 300 | - | 100 | 100 | 100 | 3 | 200 | 0 | 0 |
| `telemetry-basic` | 100 | 200 | 100 | 0 | 300 | - | 100 | 100 | 100 | 3 | 200 | 0 | 0 |
| `telemetry-none` | 100 | 200 | 0 | 0 | 0 | - | - | 100 | 100 | 0 | 200 | 0 | 0 |
| `no-debug-exporter` | 100 | 200 | 100 | 0 | 200 | - | 100 | 100 | 0 | 2 | 200 | 0 | 0 |
| `sink-stopped` | 100 | 503 | 0 | 100 | 200 | 100 | 0 | 0 | 100 | 3 | 200 | 3 | 1 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

One burst of 100 datapoints, and the surfaces do not agree.

`otelcol_exporter_sent_metric_points` reads 300 on a healthy run. Three
exporters each counted the same 100 datapoints, and the counter has an
`exporter` label that the obvious query throws away. A dashboard that sums it
without that label reports three times the traffic the pipeline carries, and
the factor changes whenever someone adds an exporter. Removing the `debug`
exporter drops the same number to 200, which is the clearest possible
demonstration that the number measures fan-out and not volume. The honest
query filters on one exporter: the `Sent to the sink` column reads 100
throughout.

The stopped sink is the case worth keeping. The collector accepted nothing:
`receiver_accepted` is 0, `receiver_refused` is 100, and the OTLP receiver
returned **503** to the sender, so with no queue in front of the exporter the
failure travelled all the way back to the client. The `debug` exporter still
printed all 100 datapoints and the Prometheus exporter still took them, which
is why `Sent, all exporters` still reads 200 while `Sent to the sink` reads 0
and `send_failed` reads 100. The health check returned **200** the whole time.
Alerting on `/` is alerting on the process being alive, and nothing else.

At `level: none` the collector publishes no `otelcol_*` counter of its own,
and the burst still arrived at the sink intact. You lose every number about
the Collector and keep the data. That is a defensible trade only if something
downstream counts for you, which is what the sink is doing here.

The author's hypothesis held on the fan-out and on the health check. It was
wrong on the stopped sink: the prediction was 100 accepted with the loss
visible only in `send_failed`, and the measurement says the receiver refused
the batch and told the sender so. The prediction underestimated how far
back-pressure travels when `sending_queue` is disabled.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with `sending_queue` and
`retry_on_failure` disabled on the OTLP exporter, which is not how a
production exporter is configured and is the reason the failure reached the
sender synchronously. With the defaults on, the queue absorbs the failure and
the receiver keeps returning 200, which is L2-01's question. One burst of 100
datapoints is below any queue, batch or memory limit, so nothing here is
evidence about behaviour under load. The `healthcheckextension` has
configurable checks, including pipeline checks, which are not enabled here.
