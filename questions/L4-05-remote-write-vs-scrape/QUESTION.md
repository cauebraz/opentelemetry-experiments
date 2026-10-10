---
id: L4-05
title: Scrape or remote write for native histograms?
level: 4
theme: metrics
status: measured
---

## Question

The same exponential histogram reaches Prometheus by being scraped from an
exporter or by being written to it. The two paths differ in what survives the
translation, in failure behaviour, and in who holds the data while the other
side is down.

## What the documentation says

On the scrape path:

> The exporter supports Prometheus native histograms. OpenTelemetry exponential
> histograms are automatically converted to the Prometheus native histogram
> format.

Source: [`exporter/prometheusexporter/README.md`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/exporter/prometheusexporter/README.md?plain=1#L73-L75),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`. Local copy:
[`vendor/prometheusexporter-README.md`](vendor/prometheusexporter-README.md).

On remote write 2.0 and native histograms:

> `io.prometheus.write.v2.Request` is the message used in Remote Write 2.0. It
> is more efficient, always includes metadata, and adds support for the created
> timestamp and native histograms. [...] PRW 2.0 support is currently **In
> Development** and is only partially implemented, thus, not ready for usage.

Source: [`exporter/prometheusremotewriteexporter/README.md`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/exporter/prometheusremotewriteexporter/README.md?plain=1#L84-L87),
same commit. Local copy:
[`vendor/prometheusremotewriteexporter-README.md`](vendor/prometheusremotewriteexporter-README.md).

A configuration pilot on this pin confirmed that
`protobuf_message: io.prometheus.write.v2.Request` is rejected unless
`exporter.prometheusremotewritexporter.enableSendingRW2` is enabled, and that
the README marks that path not ready. This question therefore measures the
supported scrape path against remote write 1.0 (`prometheus.WriteRequest`), not
PRW 1.0 and 2.0 as equivalents.

## Hypothesis

Four variants send the same Delta exponential histogram (200 observations,
one identity). `scrape-healthy` and `scrape-down` export through the
`prometheus` exporter; `remote-write-healthy` and `remote-write-down` export
through `prometheusremotewrite` with the default RW1 protobuf message. The
`-down` variants stop Prometheus during the load, read Collector self-metrics
directly from `:8888`, then start Prometheus again.

Predicted:

1. Healthy scrape delivers a native histogram that answers `histogram_quantile`
   and preserves count 200 and the sum.
2. Healthy remote write 1.0 does not deliver a queryable native histogram for
   the same exponential input; count/sum either land as something else or do
   not answer the percentile.
3. During scrape-down, the exporter holds nothing for Prometheus to miss after
   recovery beyond what a later scrape can still see; during remote-write-down,
   the exporter queue retries and `send_failed` or queue metrics move, and
   after recovery the points either arrive or are counted failed.
4. Self-metrics on `:8888` remain readable while Prometheus is stopped.

Invalid when samples are out of order without being recorded, when recovery
leaves a non-empty queue that never drains, when retries are not visible in
self-metrics, when a process restarts or is OOM-killed, or when Prometheus is
not restored. Four repetitions, variants rotated.

## How to run

```bash
just ask L4-05
```

Pins: `otelcol-contrib 0.162.0`, Prometheus `v3.15.0` with
`--web.enable-remote-write-receiver`, `scrape_native_histograms: true` on the
exported job, and protobuf scrape protocols for native histograms.

## What is measured

| Field | Meaning |
| --- | --- |
| observations | histogram count in Prometheus after recovery |
| sum | histogram sum |
| native | whether a native-histogram query returns a sample |
| p95 | `histogram_quantile(0.95, ...)` when available |
| path | `scrape` or `remote-write` |
| prometheus_down_s | how long Prometheus was stopped |
| send_failed / queue | exporter self-metrics from `:8888` during the outage |
| restored | whether Prometheus became ready again |

## Result

16 runs, 0 invalid. Healthy scrape delivered one native series
(`l4_05_duration_milliseconds`, count 200, sum 500, p95 13.929). Scrape
during an 8.4 s Prometheus outage still answered the same after recovery;
self-metrics on `:8888` stayed readable. Remote write 1.0 delivered no
series for the same Delta input; `failed_translations` was 1 and the queue
stayed empty. See [`results/REPORT.md`](results/REPORT.md).

## What this does not say

Cumulative remote write, PRW 2.0, and backends other than this Prometheus
pin are outside the run. Text scrapes still look classic; native shape
requires protobuf plus `scrape_native_histograms: true`.
