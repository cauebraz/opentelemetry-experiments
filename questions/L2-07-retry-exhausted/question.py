"""L2-07: what becomes visible when the default retry budget is exhausted."""

import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.prometheus import (
    counters_by_exporter,
    query_value,
)
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l2-07"}
METRIC_NAME = "l2_07.requests"
BURST = 100
SINK_EXPORTER = "otlp_grpc/sink"
POLL_SECONDS = 1.0
EXHAUSTION_TIMEOUT = 330.0

CONFIG = "./questions/L2-07-retry-exhausted/config/collector.yaml"

VARIANTS = (
    Variant(
        name="sink-up",
        description="the sink remains reachable",
        collector_config=CONFIG,
    ),
    Variant(
        name="sink-stopped",
        description="the sink stays stopped past the default 300s retry budget",
        collector_config=CONFIG,
        options={"fault": "stop"},
    ),
)


def burst_payload() -> dict[str, Any]:
    moment = otlp.now_unix_nano()
    points = [
        otlp.number_point(
            float(index),
            {"http.route": f"/route-{index:03d}"},
            start_unix_nano=moment - 1_000_000_000,
            time_unix_nano=moment,
        )
        for index in range(BURST)
    ]
    return otlp.metric_payload([otlp.sum_metric(METRIC_NAME, points)], SERVICE)


def send_failed() -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        'sum({__name__=~"otelcol_exporter_send_failed_metric_points(_total)?",'
        ' job="collector", exporter="otlp_grpc/sink"})',
    )


def wait_for_send_failed() -> dict[str, Any]:
    started = time.monotonic()
    while time.monotonic() - started < EXHAUSTION_TIMEOUT:
        failed = send_failed()
        if failed is not None and failed > 0:
            return {
                "retry_exhausted_seconds": round(time.monotonic() - started),
                "send_failed_at_exhaustion": failed,
                "exhaustion_observed": "yes",
            }
        time.sleep(POLL_SECONDS)
    return {
        "retry_exhausted_seconds": None,
        "send_failed_at_exhaustion": None,
        "exhaustion_observed": "no",
    }


def send(variant: Variant) -> dict[str, Any]:
    environment = stack.environment(collector_config=variant.collector_config)
    if variant.options.get("fault") == "stop":
        stack.stop_sink(environment)
    response = otlp.post_json(stack.COLLECTOR_HTTP_URL, "metrics", burst_payload())
    sent = response | {"datapoints_sent": BURST}
    if variant.options.get("fault") != "stop":
        return sent | {
            "retry_exhausted_seconds": None,
            "send_failed_at_exhaustion": None,
            "exhaustion_observed": "not applicable",
        }
    return sent | wait_for_send_failed()


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    by_exporter = counters_by_exporter(stack.PROMETHEUS_URL, "collector", "metrics")
    return {
        "sent_to_sink": by_exporter.get(SINK_EXPORTER),
        "queue_size": query_value(
            stack.PROMETHEUS_URL,
            'max({__name__="otelcol_exporter_queue_size", job="collector",'
            ' exporter="otlp_grpc/sink"})',
        ),
        "queue_size_max": query_value(
            stack.PROMETHEUS_URL,
            'max(max_over_time({__name__="otelcol_exporter_queue_size",'
            ' job="collector", exporter="otlp_grpc/sink"}[10m]))',
        ),
        "sink_up": query_value(stack.PROMETHEUS_URL, 'up{job="sink"}'),
        "retry_exhausted_seconds": sending["retry_exhausted_seconds"],
        "send_failed_at_exhaustion": sending["send_failed_at_exhaustion"],
        "exhaustion_observed": sending["exhaustion_observed"],
        "retry_log_lines": collector_log.count(
            "Exporting failed. Will retry the request after interval."
        ),
        "drop_log_lines": collector_log.count("Dropping data."),
    }


QUESTION = Question(
    id="L2-07",
    title="What becomes visible when the exporter stops retrying?",
    level=2,
    theme="collector",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Accepted", "counters.collector.metrics.receiver_accepted"),
        Column("Refused", "counters.collector.metrics.receiver_refused"),
        Column("Sent to the sink", "measured.sent_to_sink"),
        Column("Send failed", "counters.collector.metrics.exporter_send_failed"),
        Column("Failed at exhaustion", "measured.send_failed_at_exhaustion"),
        Column("Queue size max", "measured.queue_size_max"),
        Column("Queue size at end", "measured.queue_size"),
        Column("Sink target up", "measured.sink_up"),
        Column("Seconds to exhaustion", "measured.retry_exhausted_seconds"),
        Column("Retry log lines", "measured.retry_log_lines"),
        Column("Drop log lines", "measured.drop_log_lines"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
        Column("Exhaustion observed", "measured.exhaustion_observed", kind="text"),
    ),
    interpretation="""
The sender never learns the final outcome. It receives HTTP 200, and the
receiver records 100 accepted and 0 refused, in both variants. With the sink
up, all 100 are sent. With the sink stopped, the successful-send counter for
that exporter reads 0 and Prometheus `up` reads 0.

The transition L2-01 could not see happens once, at the end. During the first
20 seconds L2-01 saw no `otelcol_exporter_send_failed_metric_points`; here its
first non-zero value is exactly 100 in every repetition. It does not count
each retry attempt in this configuration. It counts the 100 datapoints the
exporter finally abandons. At the same transition the queue moves from its
maximum of 1 request to 0.

The final log is also unambiguous. Every stopped run has one `error` line:
`Exporting failed. Dropping data.`, with `dropped_items` equal to 100 and
`no more retries left` in the error field. The 12 or 13 preceding retry
notices are at `info`. The first of the three `warn` lines appears before those
retries and comes from gRPC transport state. A generic warning alert can
therefore fire early on connection noise; the exporter-scoped error arrives
only at final loss.

The timing prediction was too narrow. The author expected the first
`send_failed` sample between 300 and 330 seconds. It appeared after 265, 281
and 300 seconds. The documented 300 seconds behaves as a maximum retry budget,
not as a promise that retries continue for the whole interval.

For an alert, `otelcol_exporter_queue_size > 0` is the early signal,
`otelcol_exporter_send_failed_metric_points` is the final-loss counter, and
the error log is the final-loss event with the dropped-item count. The sender's
HTTP status remains a record of queue admission, not delivery.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with one in-memory queue request carrying
100 metric datapoints, an OTLP gRPC destination, `timeout: 5s` and the default
retry settings. Concurrent requests, a full queue, a persistent queue and a
Collector restart are different failure paths.

The one-second poll records the first Prometheus sample in which `send_failed`
is non-zero, not the Collector's internal deadline to sub-second precision.
The 265 to 300 second range proves only that `max_elapsed_time: 300s` is an
upper bound in these runs. It does not identify which backoff decision ends a
particular run early.

The sink is never recovered after exhaustion, so this does not test whether
data can be rescued near the deadline. Once the queue reaches 0 and the error
records `dropped_items: 100`, this configuration has no persistent copy to
deliver later.
""",
)
