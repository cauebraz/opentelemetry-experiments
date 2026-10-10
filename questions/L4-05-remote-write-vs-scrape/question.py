"""L4-05: scrape versus remote write 1.0 for an exponential histogram."""

import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.prometheus import (
    query_result,
    query_value,
    self_metric_totals,
    wait_for_url,
)
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L4-05-remote-write-vs-scrape/config"
SERVICE = {"service.name": "l4-05"}
METRIC = "l4_05.duration"
# UnderscoreEscapingWithSuffixes appends the unit ("ms" -> "_milliseconds").
METRIC_NAME = "l4_05_duration_milliseconds"
OBSERVATIONS = 200
SCALE = 0
POSITIVE_OFFSET = 0
# 200 observations in four buckets: 50, 50, 50, 50; values roughly 1..4 at scale 0
POSITIVE_BUCKETS = (50, 50, 50, 50)
SUM_VALUE = 500.0  # 50*1 + 50*2 + 50*3 + 50*4
QUERY_TIMEOUT = 60.0
OUTAGE_SECONDS = 8.0


def payload() -> dict[str, Any]:
    start = otlp.now_unix_nano()
    point = otlp.exponential_histogram_point(
        count=OBSERVATIONS,
        sum_value=SUM_VALUE,
        scale=SCALE,
        zero_count=0,
        positive_offset=POSITIVE_OFFSET,
        positive_buckets=POSITIVE_BUCKETS,
        point_attributes={"http.method": "GET"},
        start_unix_nano=start,
        time_unix_nano=start + 1_000_000_000,
    )
    return otlp.metric_payload(
        [otlp.exponential_histogram_metric(METRIC, [point])], SERVICE
    )


def variant(
    name: str,
    description: str,
    config: str,
    *,
    stop_prometheus: bool,
) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{DIRECTORY}/{config}",
        sink_config=stack.SINK_CONFIG,
        options={"stop_prometheus": stop_prometheus},
    )


VARIANTS = (
    variant(
        "scrape-healthy",
        "prometheus exporter, Prometheus up",
        "collector-scrape.yaml",
        stop_prometheus=False,
    ),
    variant(
        "scrape-down",
        "prometheus exporter, Prometheus stopped during the load",
        "collector-scrape.yaml",
        stop_prometheus=True,
    ),
    variant(
        "remote-write-healthy",
        "prometheusremotewrite RW1, Prometheus up",
        "collector-remote-write.yaml",
        stop_prometheus=False,
    ),
    variant(
        "remote-write-down",
        "prometheusremotewrite RW1, Prometheus stopped during the load",
        "collector-remote-write.yaml",
        stop_prometheus=True,
    ),
)


def environment(variant: Variant) -> dict[str, str]:
    return stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
    )


def _native_count_query(path: str) -> str:
    if path == "scrape":
        return f'histogram_count(sum({{__name__="{METRIC_NAME}", job="exported"}}))'
    return f'histogram_count(sum({{__name__="{METRIC_NAME}"}}))'


def _classic_count_query(path: str) -> str:
    if path == "scrape":
        return f'sum({{__name__="{METRIC_NAME}_count", job="exported"}})'
    return f'sum({{__name__="{METRIC_NAME}_count"}})'


def wait_for_metric(path: str) -> dict[str, Any]:
    deadline = time.monotonic() + QUERY_TIMEOUT
    native_count = None
    classic_count = None
    while time.monotonic() < deadline:
        classic_count = query_value(stack.PROMETHEUS_URL, _classic_count_query(path))
        native_count = query_value(stack.PROMETHEUS_URL, _native_count_query(path))
        if classic_count == OBSERVATIONS or native_count == OBSERVATIONS:
            break
        time.sleep(1.0)
    if path == "scrape":
        classic_sum = query_value(
            stack.PROMETHEUS_URL,
            f'sum({{__name__="{METRIC_NAME}_sum", job="exported"}})',
        )
        native_sum = query_value(
            stack.PROMETHEUS_URL,
            f'histogram_sum(sum({{__name__="{METRIC_NAME}", job="exported"}}))',
        )
        p95 = query_value(
            stack.PROMETHEUS_URL,
            "histogram_quantile(0.95, "
            f'sum({{__name__="{METRIC_NAME}", job="exported"}}))',
        )
        series = query_result(
            stack.PROMETHEUS_URL,
            f'{{__name__=~"{METRIC_NAME}.*", job="exported"}}',
        )
    else:
        classic_sum = query_value(
            stack.PROMETHEUS_URL,
            f'sum({{__name__="{METRIC_NAME}_sum"}})',
        )
        native_sum = query_value(
            stack.PROMETHEUS_URL,
            f'histogram_sum(sum({{__name__="{METRIC_NAME}"}}))',
        )
        p95 = query_value(
            stack.PROMETHEUS_URL,
            f'histogram_quantile(0.95, sum({{__name__="{METRIC_NAME}"}}))',
        )
        series = query_result(
            stack.PROMETHEUS_URL,
            f'{{__name__=~"{METRIC_NAME}.*"}}',
        )
    if p95 is None:
        p95 = query_value(
            stack.PROMETHEUS_URL,
            "histogram_quantile(0.95, "
            f'sum by (le) ({{__name__="{METRIC_NAME}_bucket"}}))',
        )
    names = sorted({item["metric"]["__name__"] for item in series})
    jobs = sorted({item["metric"].get("job", "") for item in series})
    return {
        "classic_count": None if classic_count is None else round(classic_count),
        "classic_sum": classic_sum,
        "native_count": None if native_count is None else round(native_count),
        "native_sum": native_sum,
        "native": native_count is not None,
        "p95": None if p95 is None else round(p95, 3),
        "series_names": names,
        "series_count": len(series),
        "jobs": jobs,
    }


def self_snapshot() -> dict[str, float | None]:
    totals = self_metric_totals(stack.COLLECTOR_SELF_METRICS_URL)
    keys = (
        "otelcol_exporter_send_failed_metric_points",
        "otelcol_exporter_sent_metric_points",
        "otelcol_exporter_queue_size",
        "otelcol_exporter_queue_capacity",
        "otelcol_exporter_prometheusremotewrite_failed_translations",
        "otelcol_exporter_prometheusremotewrite_sent_batches",
    )
    return {key: totals.get(key) for key in keys}


def send(variant: Variant) -> dict[str, Any]:
    env = environment(variant)
    stop = bool(variant.options["stop_prometheus"])
    path = "scrape" if "scrape" in variant.name else "remote-write"
    outage = 0.0
    during: dict[str, float | None] = {}
    restored = True
    if stop:
        stack.compose("stop", "prometheus", env=env)
        began = time.monotonic()
        result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "metrics", payload())
        during = self_snapshot()
        time.sleep(OUTAGE_SECONDS)
        stack.compose("start", "prometheus", env=env)
        try:
            wait_for_url(f"{stack.PROMETHEUS_URL}/-/ready")
        except TimeoutError:
            restored = False
        outage = round(time.monotonic() - began, 2)
    else:
        result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "metrics", payload())
        time.sleep(2.0)
    metrics = wait_for_metric(path) if restored else {}
    after = self_snapshot()
    state = stack.container_state("collector", env)
    return {
        "path": path,
        "http_status": result["http_status"],
        "request_bytes": result["request_bytes"],
        "observations_sent": OBSERVATIONS,
        "prometheus_down_s": outage,
        "prometheus_restored": restored,
        "during_send_failed": during.get("otelcol_exporter_send_failed_metric_points"),
        "during_sent": during.get("otelcol_exporter_sent_metric_points"),
        "during_queue_size": during.get("otelcol_exporter_queue_size"),
        "during_failed_translations": during.get(
            "otelcol_exporter_prometheusremotewrite_failed_translations"
        ),
        "after_send_failed": after.get("otelcol_exporter_send_failed_metric_points"),
        "after_sent": after.get("otelcol_exporter_sent_metric_points"),
        "after_queue_size": after.get("otelcol_exporter_queue_size"),
        "after_failed_translations": after.get(
            "otelcol_exporter_prometheusremotewrite_failed_translations"
        ),
        "after_sent_batches": after.get(
            "otelcol_exporter_prometheusremotewrite_sent_batches"
        ),
        "collector_running": state["running"],
        "collector_oom": state["oom_killed"],
        "collector_restarts": state["restarts"],
        **{f"metric_{key}": value for key, value in metrics.items()},
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    return {
        "native": sending.get("metric_native"),
        "observations": sending.get("metric_native_count")
        or sending.get("metric_classic_count"),
        "series_names": sending.get("metric_series_names"),
        "failed_translations": sending.get("after_failed_translations"),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    notes: list[str] = []
    if sending.get("http_status") != 200:
        notes.append(f"receiver returned {sending.get('http_status')}")
    if not sending.get("collector_running"):
        notes.append("the collector is not running")
    if sending.get("collector_oom"):
        notes.append("the collector was OOM-killed")
    if sending.get("collector_restarts"):
        notes.append(f"the collector restarted {sending['collector_restarts']} times")
    if variant.options["stop_prometheus"] and not sending.get("prometheus_restored"):
        notes.append("Prometheus did not come back")
    if variant.name == "scrape-healthy" and not sending.get("metric_native"):
        notes.append("healthy scrape did not expose a native histogram")
    if (
        variant.name == "scrape-healthy"
        and sending.get("metric_native_count") != OBSERVATIONS
    ):
        notes.append(
            f"healthy scrape native count was {sending.get('metric_native_count')}"
        )
    if variant.name == "remote-write-healthy" and sending.get("metric_native"):
        notes.append(
            "healthy remote write unexpectedly delivered a native histogram for Delta"
        )
    if sending.get("after_queue_size") not in (None, 0, 0.0):
        notes.append(f"exporter queue still held {sending.get('after_queue_size')}")
    return notes


QUESTION = Question(
    id="L4-05",
    title="Scrape or remote write for native histograms?",
    level=4,
    theme="metrics",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=4,
    rotate_variants=True,
    columns=(
        Column("Path", "sending.path", kind="text"),
        Column("HTTP", "sending.http_status"),
        Column("Native", "sending.metric_native", kind="text"),
        Column("Native count", "sending.metric_native_count"),
        Column("Classic count", "sending.metric_classic_count"),
        Column("Native sum", "sending.metric_native_sum", digits=1),
        Column("p95", "sending.metric_p95", digits=3),
        Column("Series", "sending.metric_series_count"),
        Column("Down s", "sending.prometheus_down_s", digits=1),
        Column("During send_failed", "sending.during_send_failed"),
        Column("During queue", "sending.during_queue_size"),
        Column("During failed_tx", "sending.during_failed_translations"),
        Column("After send_failed", "sending.after_send_failed"),
        Column("After queue", "sending.after_queue_size"),
        Column("After failed_tx", "sending.after_failed_translations"),
        Column("Restored", "sending.prometheus_restored", kind="text"),
    ),
    interpretation="""
Scrape and remote write 1.0 did not treat the same Delta exponential
histogram as equals. With `scrape_native_histograms: true` and protobuf
negotiation, every healthy scrape landed one native series named
`l4_05_duration_milliseconds`, count 200, sum 500, and
`histogram_quantile(0.95)` answered 13.929. Stopping Prometheus for about
8.4 s during the load did not lose that answer: the exporter kept the last
value, `:8888` stayed readable (`queue_size` 0), and after Prometheus came
back the same native count and p95 were there. The scrape path does not
queue toward Prometheus; recovery is whatever the exporter still exposes.

Remote write 1.0 never produced a queryable series for that Delta input.
`otelcol_exporter_prometheusremotewrite_failed_translations` rose to 1 in
every RW run, the queue stayed empty, and `send_failed` never moved. The
exporter README warns that non-cumulative histograms are dropped; the
counter is the measured form of that drop. A Cumulative pilot outside this
session can deliver a native histogram over RW1, so the failure here is the
temporality contract, not an inability of Prometheus to store native data
from remote write. PRW 2.0 remains marked not ready on this pin and was not
measured as a peer of RW1.
""",
    does_not_say="""
Measured with one Delta exponential histogram of 200 observations on
`otelcol-contrib 0.162.0` and Prometheus `v3.15.0` with
`--web.enable-remote-write-receiver` and `scrape_native_histograms: true`
on the exported job. Cumulative remote write, PRW 2.0, multi-tenant
backends, and scrape intervals other than 1 s are outside the run. Text
exposition of the prometheus exporter still shows a classic `+Inf` view;
native shape is visible only after a protobuf scrape that accepts native
histograms. Docker Desktop on Darwin arm64 is the host for these numbers.
""",
)
