"""L0-03: one burst of 100 datapoints, read through five different surfaces."""

import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.prometheus import counters_by_exporter
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l0-03"}
METRIC_NAME = "l0_03.requests"
BURST = 100
SINK_EXPORTER = "otlp_grpc/sink"

CONFIG = "./questions/L0-03-where-the-collector-tells-you/config"

VARIANTS = (
    Variant(
        name="healthy",
        description="every surface available, nothing failing",
        collector_config=f"{CONFIG}/collector-healthy.yaml",
    ),
    Variant(
        name="telemetry-basic",
        description="service.telemetry.metrics.level set to basic",
        collector_config=f"{CONFIG}/collector-telemetry-basic.yaml",
    ),
    Variant(
        name="telemetry-none",
        description="service.telemetry.metrics.level set to none",
        collector_config=f"{CONFIG}/collector-telemetry-none.yaml",
        expects_acceptance=False,
        expects_telemetry=False,
    ),
    Variant(
        name="no-debug-exporter",
        description="the same pipeline with the debug exporter removed",
        collector_config=f"{CONFIG}/collector-no-debug.yaml",
    ),
    Variant(
        name="sink-stopped",
        description="the downstream collector stopped just before the burst",
        collector_config=f"{CONFIG}/collector-healthy.yaml",
        expects_acceptance=False,
        options={"stop_sink": True},
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


def health() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(stack.COLLECTOR_HEALTH_URL, timeout=5) as response:
            return {"health_status_code": response.status}
    except urllib.error.HTTPError as error:
        return {"health_status_code": error.code}
    except OSError:
        return {"health_status_code": 0}


def send(variant: Variant) -> dict[str, Any]:
    if variant.options.get("stop_sink"):
        stack.stop_sink(stack.environment(collector_config=variant.collector_config))
    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "metrics", burst_payload())
    return result | {"datapoints_sent": BURST} | health()


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    text = stack.unprefixed(collector_log)
    by_exporter = counters_by_exporter(stack.PROMETHEUS_URL, "collector", "metrics")
    return {
        "sent_to_sink": by_exporter.get(SINK_EXPORTER),
        "exporters_reporting": len(by_exporter),
        "exporters": ", ".join(sorted(by_exporter)) or "none",
        "debug_datapoints": len(
            re.findall(r"^NumberDataPoints #\d+$", text, re.MULTILINE)
        ),
        "health_status_code": sending["health_status_code"],
    }


QUESTION = Question(
    id="L0-03",
    title="Which surface tells you what the Collector actually did?",
    level=0,
    theme="collector",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("Sent by us", "sending.datapoints_sent"),
        Column("HTTP status", "sending.http_status"),
        Column("Accepted", "counters.collector.metrics.receiver_accepted"),
        Column("Refused", "counters.collector.metrics.receiver_refused"),
        Column("Sent, all exporters", "counters.collector.metrics.exporter_sent"),
        Column("Send failed", "counters.collector.metrics.exporter_send_failed"),
        Column("Sent to the sink", "measured.sent_to_sink"),
        Column("Arrived at the sink", "counters.sink.metrics.receiver_accepted"),
        Column("Printed by debug", "measured.debug_datapoints"),
        Column("Exporters counted", "measured.exporters_reporting"),
        Column("Health status", "measured.health_status_code"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
    ),
    interpretation="""
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
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with `sending_queue` and
`retry_on_failure` disabled on the OTLP exporter, which is not how a
production exporter is configured and is the reason the failure reached the
sender synchronously. With the defaults on, the queue absorbs the failure and
the receiver keeps returning 200, which is L2-01's question. One burst of 100
datapoints is below any queue, batch or memory limit, so nothing here is
evidence about behaviour under load. The `healthcheckextension` has
configurable checks, including pipeline checks, which are not enabled here.
""",
)
