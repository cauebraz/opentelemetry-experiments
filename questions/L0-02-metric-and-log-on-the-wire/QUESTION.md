---
id: L0-02
title: What survives when a metric or a log is missing a field?
level: 0
theme: metrics
status: measured
---

## Question

Spans are forgiving: a span with a field missing is still a span. Metrics are
not, because a sum without a temporality is arithmetically undefined, and logs
are not, because a log without a timestamp has to be placed somewhere on a
time axis by whoever stores it.

Four payloads go to the same receiver: a delta sum, a sum with no
`aggregationTemporality` at all, a gauge, and two log records, one with no
timestamp and one carrying a `trace_id`. The question is which of them the
Collector refuses, which it accepts and quietly repairs, and which it accepts
and passes on broken.

The last case is the expensive one. A payload refused at the door is a bug you
find in an afternoon. A payload accepted and passed on broken is a dashboard
that is wrong for a quarter.

## What the documentation says

On temporality, from the comment on the enum's zero value:

> UNSPECIFIED is the default AggregationTemporality, it MUST not be used.

Source: [`metrics.proto`, line 298](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.11.1/opentelemetry/proto/metrics/v1/metrics.proto#L298),
`opentelemetry-proto v1.11.1`.

On log timestamps, from the comment on `LogRecord.time_unix_nano`:

> time_unix_nano is the time when the event occurred. Value is UNIX Epoch
> time in nanoseconds since 00:00:00 UTC on 1 January 1970. Value of 0
> indicates unknown or missing timestamp.

Source: [`logs.proto`, lines 139-141](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.11.1/opentelemetry/proto/logs/v1/logs.proto#L139-L141),
`opentelemetry-proto v1.11.1`.

Both quotes checked against the raw files at the references above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-proto/v1.11.1/opentelemetry/proto/metrics/v1/metrics.proto | grep -n 'UNSPECIFIED is the default'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-proto/v1.11.1/opentelemetry/proto/logs/v1/logs.proto | grep -n 'Value of 0 indicates unknown'
```

What the paragraphs leave open: "MUST not be used" is addressed to the sender.
They do not say what the receiver does when a sender uses it anyway, whether
anything in the pipeline objects, or whether the datapoint reaches an exporter
that has to decide what the number means.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `delta-sum` | HTTP 200, 1 accepted metric point, 1 series on the Prometheus exporter, no warning |
| `sum-without-temporality` | HTTP 200 and 1 accepted metric point, because the field is simply absent from valid JSON, and then the Prometheus exporter **drops it with no series and no error**, because it cannot decide between a counter and a reset |
| `gauge` | HTTP 200, 1 accepted metric point, 1 series, no warning |
| `log-without-timestamp` | HTTP 200, 1 accepted log record, the `debug` exporter printing a zero timestamp, no warning |
| `log-with-trace-id` | HTTP 200, 1 accepted log record, the `debug` exporter printing the trace id, no warning |

The prediction the author cares about is `sum-without-temporality`: accepted at
the receiver, counted as accepted, and then absent from the exporter, so the
accepted counter and the data a backend holds disagree and nothing in between
says why.

## How to run

```bash
just ask L0-02
```

Without the harness, the sum with no temporality by hand:

```bash
just up
curl -i -X POST http://127.0.0.1:24318/v1/metrics \
  -H 'Content-Type: application/json' \
  -d '{"resourceMetrics":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"l0-02"}}]},"scopeMetrics":[{"scope":{"name":"opentelemetry_experiments"},"metrics":[{"name":"l0_02.requests","unit":"1","sum":{"isMonotonic":true,"dataPoints":[{"asDouble":7,"startTimeUnixNano":"1760000000000000000","timeUnixNano":"1760000001000000000","attributes":[]}]}}]}]}]}'
curl -s http://127.0.0.1:29464/metrics | grep l0_02
just logs collector
just down
```

## What is measured

Held constant: one request per variant, OTLP/HTTP JSON, the shared
[`config/collector-debug.yaml`](../../config/collector-debug.yaml) with no
processors. What varies is which field is left out.

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the status the OTLP receiver returned |
| `counters.collector.metrics.receiver_accepted` | `otelcol_receiver_accepted_metric_points` |
| `counters.collector.logs.receiver_accepted` | `otelcol_receiver_accepted_log_records` |
| `counters.sink.metrics.receiver_accepted` | what the second collector actually received |
| `measured.exported_series` | series the Prometheus exporter published for this metric; metric variants only |
| `measured.debug_records` | metric points or log records the `debug` exporter printed |
| `measured.timestamp_is_the_zero_instant` | whether the timestamp it printed is `1970-01-01 00:00:00 +0000 UTC`; log variants only |
| `measured.trace_id_hex_length` | hex characters in the trace id printed on the log record; log variants only, `-` for metrics |
| `log.warn_lines` | warnings in the collector log for the whole run |

No wall-clock value is recorded, only what the printed time means.

## Result

[`results/REPORT.md`](results/REPORT.md), 15 runs, none invalid.

The hypothesis held on all five variants. The sum with no
`aggregationTemporality` returned 200, was counted as one accepted metric
point, arrived at the sink as one metric point, and produced zero series on
the Prometheus exporter, with zero warnings in the collector log. The delta
sum and the gauge each produced one series. The log record with no timestamp
was accepted and printed at the Unix epoch.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with the `prometheus` exporter. A
different exporter can make a different decision about a sum with no
temporality, and a vendor OTLP endpoint is free to accept it. One datapoint
per request says nothing about a batch where one point out of a thousand is
malformed. Nothing here measures what a backend does with a log record it
stored at the Unix epoch.
