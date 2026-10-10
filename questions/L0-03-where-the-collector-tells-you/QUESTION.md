---
id: L0-03
title: Which surface tells you what the Collector actually did?
level: 0
theme: collector
status: measured
---

## Question

The same burst of 100 datapoints goes through the same pipeline five times.
What changes is what you are allowed to look at: the `debug` exporter, the
`otelcol_*` self-metrics at three telemetry levels, the health check endpoint,
and a second collector downstream that counts what truly arrived.

Nobody debugs a pipeline with all five. In production you have one or two, and
the question is which of them would have told you the truth. The variant where
the downstream collector is stopped is the one that matters: five surfaces,
one burst, and they do not all say the same thing.

## What the documentation says

On the health check, from the extension's README:

> Health Check extension enables an HTTP url that can be probed to check the
> status of the OpenTelemetry Collector. This extension can be used as a
> liveness and/or readiness probe on Kubernetes.

Source: [`healthcheckextension/README.md`, lines 33-35](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.162.0/extension/healthcheckextension/README.md?plain=1#L33-L35),
`opentelemetry-collector-contrib v0.162.0`. The same README opens with a
migration notice saying the extension "is migrating to use component status
reporting for health checks".

On the telemetry level, from the list of values for `level`:

> `none`: no telemetry is collected.

Source: [`internal-telemetry.md`, line 139](https://github.com/open-telemetry/opentelemetry.io/blob/fb0a807365b1b7a90ed3315cd4c4e46e44a1b48e/content/en/docs/collector/internal-telemetry.md?plain=1#L139),
`opentelemetry.io` at commit `fb0a807365b1`, rendered at
[Internal telemetry, "Metric verbosity"](https://opentelemetry.io/docs/collector/internal-telemetry/#metric-verbosity).

Both quotes checked against the raw files at the references above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/extension/healthcheckextension/README.md | grep -n 'enables an HTTP url'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/fb0a807365b1b7a90ed3315cd4c4e46e44a1b48e/content/en/docs/collector/internal-telemetry.md | grep -n 'no telemetry is collected'
```

What the paragraphs leave open: "the status of the Collector" is not defined
as "the Collector is delivering data", and a value of `none` is described by
what it stops collecting rather than by what still works without it. Neither
page says which number you should put on a dashboard.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `healthy` | 100 accepted, 100 arriving at the sink, 100 printed by `debug`, health 200, and `otelcol_exporter_sent_metric_points` reporting **300**, because three exporters each count the same 100 |
| `telemetry-basic` | the four delivery counters still present, the per-component detail reduced, data unaffected |
| `telemetry-none` | no `otelcol_*` series at all, so the collector job in Prometheus has nothing, while the sink still receives 100 |
| `no-debug-exporter` | counters unchanged except that one fewer exporter reports sent, so the total drops from 300 to 200, and nothing is printed |
| `sink-stopped` | 100 accepted, 0 arriving, `send_failed` above zero, and the health check **still returning 200** |

Two predictions the author cares about. First, that `otelcol_exporter_sent_*`
summed without an `exporter` label counts fan-out, so a pipeline with three
exporters reports three times the traffic it has, and a dashboard built on the
sum is wrong by a factor nobody notices. Second, that the health check returns
200 while the data is going nowhere, so an alert on `/` is an alert on the
process being alive and on nothing else.

## How to run

```bash
just ask L0-03
```

## What is measured

Held constant: one request of 100 datapoints on one metric, OTLP/HTTP JSON.
What varies is the collector config and whether the sink is running.

| Field | Meaning |
| --- | --- |
| `counters.collector.metrics.receiver_accepted` | `otelcol_receiver_accepted_metric_points` |
| `counters.collector.metrics.exporter_sent` | `otelcol_exporter_sent_metric_points`, summed over every exporter |
| `counters.collector.metrics.exporter_send_failed` | the same, for failures |
| `counters.sink.metrics.receiver_accepted` | what the second collector received, which is the ground truth |
| `measured.sent_to_sink` | the sent counter restricted to the OTLP exporter |
| `measured.exporters_reporting` | how many exporters contribute to the unlabelled sum |
| `measured.debug_datapoints` | datapoints the `debug` exporter printed |
| `measured.health_status_code` | the HTTP status of the health check during the burst |
| `log.warn_lines`, `log.error_lines` | the collector log, counted by level |

The `sink-stopped` variant stops the sink after the stack is healthy and
before the burst, so the collector starts with a working downstream and loses
it, which is the ordinary production case.

## Result

[`results/REPORT.md`](results/REPORT.md), 15 runs, none invalid.

The hypothesis held on the fan-out and on the health check:
`otelcol_exporter_sent_metric_points` reads 300 for 100 datapoints with three
exporters and 200 with two, and the health check returned 200 in every
variant including the one losing all its data.

It was wrong on `sink-stopped`. The prediction was 100 accepted with the loss
visible only in `send_failed`. The measurement says the OTLP receiver
returned **503** to the sender, counted 100 refused and 0 accepted, and
`send_failed` read 100 while the `debug` and `prometheus` exporters still
took their copies. With `sending_queue` disabled, back-pressure travels
further back than the hypothesis assumed.

One correction was made to the harness after the first run and before the
numbers above: the `sink-stopped` variant was marked `expects_acceptance=False`
and `wait_until_stable` gained a floor for believing an all-zero reading,
because the first attempt settled on "no counters exist" before Prometheus had
scraped them. That is a validity rule, not a prediction, and the hypothesis
above is as it was written.

## What this does not say

Measured on `otelcol-contrib 0.162.0`. This question is about which surface
reports what, not about how much data a failure costs: the OTLP exporter here
has `sending_queue` and `retry_on_failure` disabled so that a failure is
immediate and countable, which is not how a production exporter is
configured. L2-01 is the question that turns them back on and varies them.
One burst of 100 datapoints is below any queue or memory limit, so
nothing here is evidence about behaviour under load. The health check
extension has configurable checks that are not enabled here, and its README
at this version says it is migrating to component status reporting, so a
later version may answer the stopped-sink case differently.
