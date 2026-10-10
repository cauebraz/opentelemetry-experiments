"""L0-02: a metric and a log with one field missing each."""

import re
import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.prometheus import query_scalar
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l0-02"}
COUNTER_NAME = "l0_02.requests"
GAUGE_NAME = "l0_02.inflight"
EXPORTED_SELECTOR = '{__name__=~"l0_02.*", job="exported"}'

VARIANTS = (
    Variant(
        name="delta-sum",
        description="a monotonic sum with delta temporality",
        options={"signal": "metrics", "temporality": otlp.DELTA},
    ),
    Variant(
        name="sum-without-temporality",
        description="the same sum with no aggregationTemporality field",
        options={"signal": "metrics", "temporality": None},
    ),
    Variant(
        name="gauge",
        description="a gauge, which has no temporality to leave out",
        options={"signal": "metrics", "gauge": True},
    ),
    Variant(
        name="log-without-timestamp",
        description="a log record with no timeUnixNano",
        options={"signal": "logs"},
    ),
    Variant(
        name="log-with-trace-id",
        description="a log record with a timestamp and a trace id",
        options={"signal": "logs", "timestamp": True, "trace_id": "a" * 32},
    ),
)


def payload_for(variant: Variant) -> dict[str, Any]:
    moment = otlp.now_unix_nano()
    if variant.options["signal"] == "metrics":
        point = otlp.number_point(
            7.0,
            {"http.method": "GET"},
            start_unix_nano=moment - 1_000_000_000,
            time_unix_nano=moment,
        )
        metric = (
            otlp.gauge_metric(GAUGE_NAME, [point])
            if variant.options.get("gauge")
            else otlp.sum_metric(
                COUNTER_NAME,
                [point],
                temporality=variant.options["temporality"],
            )
        )
        return otlp.metric_payload([metric], SERVICE)
    record = otlp.log_record(
        "checkout failed",
        severity_number=17,
        severity_text="ERROR",
        time_unix_nano=moment if variant.options.get("timestamp") else None,
        trace_id=variant.options.get("trace_id"),
        span_id="b" * 16 if variant.options.get("trace_id") else None,
    )
    return otlp.log_payload([record], SERVICE)


def send(variant: Variant) -> dict[str, Any]:
    signal = variant.options["signal"]
    payload = payload_for(variant)
    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, signal, payload)
    return result | {
        "curl": otlp.curl_command(stack.COLLECTOR_HTTP_URL, signal, payload)
    }


ZERO_INSTANT = "1970-01-01 00:00:00 +0000 UTC"
TIMESTAMP = re.compile(r"^Timestamp:\s*(.*?)\s*$", re.MULTILINE)
TRACE_ID = re.compile(r"^Trace ID:\s*(\S*)\s*$", re.MULTILINE)


def exported_series(timeout_seconds: float = 10.0) -> int:
    """Poll briefly, so a zero means absent rather than not scraped yet."""
    deadline = time.monotonic() + timeout_seconds
    count = 0
    while time.monotonic() < deadline:
        count = round(query_scalar(stack.PROMETHEUS_URL, f"count({EXPORTED_SELECTOR})"))
        if count:
            return count
        time.sleep(0.5)
    return count


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    text = stack.unprefixed(collector_log)
    metrics_variant = variant.options["signal"] == "metrics"
    timestamps = TIMESTAMP.findall(text)
    trace_ids = [value for value in TRACE_ID.findall(text) if value]
    return {
        "exported_series": exported_series() if metrics_variant else None,
        "debug_records": len(
            re.findall(r"^(?:NumberDataPoints|LogRecord) #\d+$", text, re.MULTILINE)
        ),
        "timestamp_is_the_zero_instant": (
            None
            if metrics_variant or not timestamps
            else ("yes" if timestamps[-1] == ZERO_INSTANT else "no")
        ),
        "trace_id_hex_length": (
            None if metrics_variant else (len(trace_ids[-1]) if trace_ids else 0)
        ),
    }


QUESTION = Question(
    id="L0-02",
    title="What survives when a metric or a log is missing a field?",
    level=0,
    theme="metrics",
    signals=("metrics", "logs"),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Accepted points", "counters.collector.metrics.receiver_accepted"),
        Column("Accepted logs", "counters.collector.logs.receiver_accepted"),
        Column("Sink points", "counters.sink.metrics.receiver_accepted"),
        Column("Sink logs", "counters.sink.logs.receiver_accepted"),
        Column("Exported series", "measured.exported_series"),
        Column("Printed by debug", "measured.debug_records"),
        Column("Warn lines", "log.warn_lines"),
        Column(
            "Timestamp is 1970", "measured.timestamp_is_the_zero_instant", kind="text"
        ),
        Column("Trace id hex chars", "measured.trace_id_hex_length"),
    ),
    interpretation="""
Every one of the five payloads came back 200. None of them was refused, and
the collector log carried zero warnings in all fifteen runs.

The sum with no `aggregationTemporality` was accepted as one metric point,
reached the sink as one metric point, and produced zero series on the
Prometheus exporter. The delta sum and the gauge each produced one. So the
datapoint crossed the whole pipeline, was counted as delivered by every
counter on the way, and then disappeared at the exporter with nothing written
anywhere about it.

That is the shape of the failure worth remembering. A missing series is not
visible on a dashboard, because a dashboard shows series that exist. The
alert that would have fired on the metric never fires, because there is no
metric. `otelcol_receiver_accepted_metric_points` and
`otelcol_exporter_sent_metric_points` both agree that the data moved, and the
only way to notice is to go looking for a metric you expected and find it
absent.

The two log records behaved as the protocol describes. The record with no
`timeUnixNano` was accepted and printed with the timestamp at the Unix epoch,
which places the event at 1970 for whoever stores it. The record carrying a
32 character trace id kept it intact through the pipeline, which is what makes
a log clickable from a trace.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with the `prometheus` exporter. A
different exporter can make a different decision about a sum with no
temporality, and a vendor OTLP endpoint is free to accept and store it. One
datapoint per request says nothing about a batch where one point out of a
thousand is malformed, nor about whether the drop would be visible at that
ratio. Nothing here measures what a backend does with a log record it stored
at the Unix epoch, only that the Collector hands it over that way.
""",
)
