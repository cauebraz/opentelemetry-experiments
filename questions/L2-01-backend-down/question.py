"""L2-01: the sink paused and the sink stopped, with the queue at its defaults."""

import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.prometheus import counters_by_exporter, query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l2-01"}
METRIC_NAME = "l2_01.requests"
BURST = 100
SINK_EXPORTER = "otlp_grpc/sink"

HOLD_SECONDS = 20.0
RECOVERY_TIMEOUT = 60.0

CONFIG = "./questions/L2-01-backend-down/config/collector-queue-defaults.yaml"

VARIANTS = (
    Variant(
        name="sink-up",
        description="the sink reachable throughout",
        collector_config=CONFIG,
    ),
    Variant(
        name="sink-paused",
        description="the sink paused for 20s, which holds the connection open",
        collector_config=CONFIG,
        options={"fault": "pause", "recover": True},
    ),
    Variant(
        name="sink-stopped-restarted",
        description="the sink stopped for 20s, then started again",
        collector_config=CONFIG,
        options={"fault": "stop", "recover": True},
    ),
    Variant(
        name="sink-stopped",
        description="the sink stopped before the burst and never started",
        collector_config=CONFIG,
        options={"fault": "stop", "recover": False},
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


def arrived_at_sink() -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        'sum({__name__=~"otelcol_receiver_accepted_metric_points(_total)?",'
        ' job="sink"})',
    )


def wait_for_drain(target: float, stable_rounds: int = 2) -> float | None:
    """Seconds from recovery until the target arrives and stops increasing."""
    started = time.monotonic()
    previous: float | None = None
    repeats = 0
    while time.monotonic() - started < RECOVERY_TIMEOUT:
        current = arrived_at_sink()
        settled = current is not None and current >= target and current == previous
        repeats = repeats + 1 if settled else 0
        if repeats >= stable_rounds:
            return round(time.monotonic() - started)
        previous = current
        time.sleep(1.0)
    return None


def send(variant: Variant) -> dict[str, Any]:
    """The fault is applied before the burst and lifted after a fixed hold."""
    environment = stack.environment(collector_config=variant.collector_config)
    fault = variant.options.get("fault")
    if fault == "pause":
        stack.pause_sink(environment)
    elif fault == "stop":
        stack.stop_sink(environment)

    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "metrics", burst_payload())
    sent = result | {"datapoints_sent": BURST}
    if fault is None:
        return sent | {"recovered": "not applicable", "drain_seconds": None}

    time.sleep(HOLD_SECONDS)
    if not variant.options.get("recover"):
        return sent | {"recovered": "no", "drain_seconds": None}

    if fault == "pause":
        stack.unpause_sink(environment)
    else:
        stack.start_sink(environment)
    return sent | {
        "recovered": "yes",
        "drain_seconds": wait_for_drain(BURST),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    by_exporter = counters_by_exporter(stack.PROMETHEUS_URL, "collector", "metrics")
    return {
        "sent_to_sink": by_exporter.get(SINK_EXPORTER),
        "queue_size_max": query_value(
            stack.PROMETHEUS_URL,
            'max(max_over_time({__name__="otelcol_exporter_queue_size",'
            ' job="collector"}[5m]))',
        ),
        "queue_capacity": query_value(
            stack.PROMETHEUS_URL,
            'max({__name__="otelcol_exporter_queue_capacity", job="collector"})',
        ),
        "enqueue_failed": query_value(
            stack.PROMETHEUS_URL,
            'sum({__name__=~"otelcol_exporter_enqueue_failed_metric_points(_total)?",'
            ' job="collector"})',
        ),
        "sink_up": query_value(stack.PROMETHEUS_URL, 'up{job="sink"}'),
        "drain_seconds": sending["drain_seconds"],
        "recovered": sending["recovered"],
    }


QUESTION = Question(
    id="L2-01",
    title="What happens to a burst when the backend is down and the queue is on?",
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
        Column("Enqueue failed", "measured.enqueue_failed"),
        Column("Queue size max", "measured.queue_size_max"),
        Column("Queue capacity", "measured.queue_capacity"),
        Column("Arrived at the sink", "counters.sink.metrics.receiver_accepted"),
        Column("Sink target up", "measured.sink_up"),
        Column("Seconds to drain", "measured.drain_seconds"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
        Column("Recovered", "measured.recovered", kind="text"),
    ),
    interpretation="""
With the queue and the retry at their defaults, a backend outage is invisible
to the sender and to the export-failure counter during the measured window.

The HTTP status is 200 in all four variants, including the run where the sink
target is down and no successful sink export is recorded. The receiver accepts
100 and refuses 0 in every variant. L0-03 measured a 503 and 100 refused for
the same outage with the queue disabled, so turning the queue on, which is the
default, converts a delivery failure into a sender-side success. No client
error rate can detect this.

`otelcol_exporter_send_failed_metric_points` was never published. Not in the
paused run, not in the run that was stopped and restarted, and not in the run
where the sink remained unavailable and no successful export was recorded.
The exports did fail: the collector log carries two
`Exporting failed. Will retry the request after interval.` lines in each
paused run and three or four in each stopped run. The counter counts none of
those retry attempts during the 20 second hold. That is consistent with a
counter that changes only after the retry budget is exhausted, but this run
does not establish when it appears. An alert on the counter would not have
fired during the measured outage.

The log does not help either. Those retry lines are logged at `info`. A
paused run ends with zero `warn` and zero `error` lines, so a dashboard
counting log levels sees a quiet collector while every export is timing out.
The stopped runs produce one or two `warn` lines, and they come from the gRPC
client ("connect: connection refused"), not from the exporter. One detail
worth knowing: the retry line carries a field named `error`, so a text search
for the word finds it while a level filter does not.

Two faults that look the same in a diagram behave differently and both end up
delivering. A paused peer holds the connection and each attempt dies on the
exporter's 5 second timeout with `DeadlineExceeded`, which fits two attempts
into the 20 second hold. A stopped peer refuses the connection, each attempt
fails at once with `Unavailable`, and three or four fit in the hold. After
recovery, the paused sink reached 100 and stayed there sooner than the
restarted sink in all three repetitions. The saved log records every jittered
interval, so the retry schedule can be inspected without treating the count of
log levels as the evidence.

`Queue size max` reads 1 and `Queue capacity` reads 1000. Both are in
requests, because the default `sizer` is `requests`, so the burst of 100
datapoints occupies one slot of a thousand. Sizing that queue by thinking in
datapoints overstates the buffer by whatever the batch size turns out to be.

The practical conclusion is that sender status and `send_failed` are not enough
to detect this. The signals that moved were
`otelcol_exporter_queue_size`, a Collector gauge sitting at 1 for the length
of the outage, and Prometheus `up` for the destination, which read 0 when the
sink stayed stopped. Alert on destination availability, on its arrival rate
while it is scrapeable, and on a queue that is not draining. Do not rely on
`send_failed` for the first 20 seconds, which is the only interval measured
here.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with one burst of 100 datapoints, a
20 second outage and `timeout: 5s` on the exporter. Every run ended well before
`max_elapsed_time` of 300 seconds, so this says nothing about what the
Collector does when the retry budget runs out: whether
`otelcol_exporter_send_failed_metric_points` appears then, and whether anything
is logged above `info`, is the obvious next question and is not answered here.
The stopped sink has no current accepted-counter sample; `Sink target up` is 0,
and `Arrived at the sink` is `-`, not a measured zero.

The queue was never filled. One request against a capacity of a thousand means
`block_on_overflow`, `otelcol_exporter_enqueue_failed_*` and the documented
"typically dropped" behaviour were never exercised. The `Enqueue failed`
column is `-` everywhere because the series was absent. This run does not show
what value it takes after the first enqueue rejection.

The queue is in memory. A persistent queue backed by a storage extension is a
different component with different guarantees, and a Collector restarted
during the outage would have lost the burst that this configuration kept.
""",
)
