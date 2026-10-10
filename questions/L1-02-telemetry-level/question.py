"""L1-02: the published per-level metric list, checked against what is emitted."""

from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.prometheus import series_names
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l1-02"}
METRIC_NAME = "l1_02.requests"
BURST = 100

CONFIG = "./questions/L1-02-telemetry-level/config"

BASIC = (
    "otelcol_exporter_enqueue_failed_log_records",
    "otelcol_exporter_enqueue_failed_metric_points",
    "otelcol_exporter_enqueue_failed_spans",
    "otelcol_exporter_in_flight_requests",
    "otelcol_exporter_queue_capacity",
    "otelcol_exporter_queue_size",
    "otelcol_exporter_send_failed_log_records",
    "otelcol_exporter_send_failed_metric_points",
    "otelcol_exporter_send_failed_spans",
    "otelcol_exporter_sent_log_records",
    "otelcol_exporter_sent_metric_points",
    "otelcol_exporter_sent_spans",
    "otelcol_process_cpu_seconds",
    "otelcol_process_memory_rss",
    "otelcol_process_runtime_heap_alloc_bytes",
    "otelcol_process_runtime_total_alloc_bytes",
    "otelcol_process_runtime_total_sys_memory_bytes",
    "otelcol_process_uptime",
    "otelcol_processor_incoming_items",
    "otelcol_processor_outgoing_items",
    "otelcol_receiver_accepted_log_records",
    "otelcol_receiver_accepted_metric_points",
    "otelcol_receiver_accepted_spans",
    "otelcol_receiver_refused_log_records",
    "otelcol_receiver_refused_metric_points",
    "otelcol_receiver_refused_spans",
    "otelcol_scraper_errored_metric_points",
    "otelcol_scraper_scraped_metric_points",
)

NORMAL = (
    "otelcol_processor_batch_batch_send_size",
    "otelcol_processor_batch_batch_size_trigger_send",
    "otelcol_processor_batch_metadata_cardinality",
    "otelcol_processor_batch_timeout_trigger_send",
)

DETAILED = (
    "http.client.request.body.size",
    "http.client.request.duration",
    "http.server.request.body.size",
    "http.server.request.duration",
    "http.server.response.body.size",
    "otelcol_exporter_enqueue_size",
    "otelcol_exporter_enqueue_size_bytes",
    "otelcol_processor_batch_batch_send_size_bytes",
    "rpc.client.call.duration",
    "rpc.server.call.duration",
)

DOCUMENTED = {
    "none": (),
    "basic": BASIC,
    "normal": BASIC + NORMAL,
    "detailed": BASIC + NORMAL + DETAILED,
}

PUBLISHED_PREFIXES = ("otelcol_", "http_", "rpc_")
TYPE_SUFFIXES = ("_bucket", "_sum", "_count", "_total")
UNIT_SUFFIXES = ("_seconds", "_bytes")

VARIANTS = tuple(
    Variant(
        name=f"level-{level}",
        description=f"service.telemetry.metrics.level set to {level}",
        collector_config=f"{CONFIG}/collector-level-{level}.yaml",
        expects_acceptance=level != "none",
        expects_telemetry=level != "none",
        options={"level": level},
    )
    for level in ("none", "basic", "normal", "detailed")
)


def normalize(name: str) -> str:
    """One family per name: dots become underscores, type suffixes come off."""
    family = name.replace(".", "_")
    for suffix in TYPE_SUFFIXES:
        if family.endswith(suffix):
            family = family[: -len(suffix)]
    return family


def accepted_forms(documented: str) -> set[str]:
    """The reader runs `without_units`, so a unit suffix may not reach the name."""
    family = normalize(documented)
    forms = {family}
    for suffix in UNIT_SUFFIXES:
        if family.endswith(suffix):
            forms.add(family[: -len(suffix)])
    return forms


def published(job: str = "collector") -> set[str]:
    return {
        normalize(name)
        for name in series_names(stack.PROMETHEUS_URL, job)
        if normalize(name).startswith(PUBLISHED_PREFIXES)
    }


def send(variant: Variant) -> dict[str, Any]:
    """One batch per signal, so every documented signal family has a chance."""
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
    sent = {
        "metrics": otlp.post_json(
            stack.COLLECTOR_HTTP_URL,
            "metrics",
            otlp.metric_payload([otlp.sum_metric(METRIC_NAME, points)], SERVICE),
        ),
        "traces": otlp.post_json(
            stack.COLLECTOR_HTTP_URL,
            "traces",
            otlp.trace_payload([otlp.span("GET /checkout")], SERVICE),
        ),
        "logs": otlp.post_json(
            stack.COLLECTOR_HTTP_URL,
            "logs",
            otlp.log_payload([otlp.log_record("one line")], SERVICE),
        ),
    }
    return {
        "http_status": max(result["http_status"] for result in sent.values()),
        "datapoints_sent": BURST,
        "per_signal_status": {
            signal: result["http_status"] for signal, result in sent.items()
        },
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    level = variant.options["level"]
    names = published()
    documented = DOCUMENTED[level]
    missing = sorted(name for name in documented if not accepted_forms(name) & names)
    without_unit = sum(
        1
        for name in documented
        if normalize(name) not in names and accepted_forms(name) & names
    )
    every_documented = {
        form for name in DOCUMENTED["detailed"] for form in accepted_forms(name)
    }
    extra = sorted(names - every_documented)
    return {
        "metric_families_published": len(names),
        "published_names": ", ".join(sorted(names)) or "none",
        "documented_for_level": len(documented),
        "documented_present": len(documented) - len(missing),
        "documented_missing": len(missing),
        "missing_names": ", ".join(missing) or "none",
        "undocumented_present": len(extra),
        "undocumented_names": ", ".join(extra) or "none",
        "unit_suffix_dropped": without_unit,
        "batch_metrics_present": sum(
            1 for name in NORMAL if accepted_forms(name) & names
        ),
    }


QUESTION = Question(
    id="L1-02",
    title="Does the documented per-level metric list match what the Collector emits?",
    level=1,
    theme="collector",
    signals=("traces", "metrics", "logs"),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Metric families published", "measured.metric_families_published"),
        Column("Documented at level", "measured.documented_for_level"),
        Column("Of those, present", "measured.documented_present"),
        Column("Of those, missing", "measured.documented_missing"),
        Column("Undocumented present", "measured.undocumented_present"),
        Column("Batch metrics of 4", "measured.batch_metrics_present"),
        Column("Matched without unit", "measured.unit_suffix_dropped"),
        Column("Accepted points", "counters.collector.metrics.receiver_accepted"),
        Column("Published names", "measured.published_names", kind="text"),
        Column("Missing names", "measured.missing_names", kind="text"),
    ),
    interpretation="""
The table is not a checklist. At `basic` the documentation lists 28 names and
19 metric families exist, and only 16 of those 19 are on the list. At `detailed` it
lists 42 and 25 of them exist. Every absence has a reason, and the reasons are
not the level.

Seven of the twelve names absent at `basic` belong to components that are not
there: `otelcol_scraper_*` needs a scraping receiver, and the three
`otelcol_exporter_enqueue_failed_*` names plus
`otelcol_exporter_queue_capacity` and `otelcol_exporter_queue_size` need
`sending_queue`. Two more, `otelcol_processor_incoming_items` and
`otelcol_processor_outgoing_items`, are absent even though the pipeline has a
`batch` processor: they are declared in `processorhelper`, and
`batchprocessor` records through its own telemetry builder instead. A reader
of the table has no way to know that.

The remaining three are the ones that matter on a dashboard.
`otelcol_exporter_send_failed_log_records`, `_metric_points` and `_spans` do
not exist on a run where nothing failed, while
`otelcol_receiver_refused_spans` and its siblings do exist and read zero on
the same run. Both are counters at `basic`, both were listed, and one is a
flat zero line while the other is "no data". The difference is whether the
component records a zero, not whether the level includes the metric. This is
L0-01's finding one layer up: a panel on `send_failed` is empty on a healthy
collector and empty on a collector whose metric was never registered, and the
two look identical.

Going from `basic` to `normal` adds exactly the four `otelcol_processor_batch_*`
names the table says it adds, which is the one place the page is precise and
correct. Going to `detailed` adds five of its ten: the three `http.server.*`
families, `rpc.client.call.duration` and
`otelcol_processor_batch_batch_send_size_bytes`. The five it does not add are
the mirror image of the traffic, `http.client.*` for an HTTP export that never
happens and `rpc.server.call.duration` for a gRPC request that never arrives,
plus the two queue sizes.

Three names appear at every enabled level and in no table:
`otelcol_receiver_failed_spans`, `_metric_points` and `_log_records`. They are
declared in `receiverhelper` at this version, described as items that "failed
to be processed by the receiver due to internal errors", and the page's lists
do not mention them. An operator reading only the documentation does not know
the metric exists.

Two predictions failed. The author expected the pull reader's
`without_units: true` to shorten the unit-carrying names, so that
`otelcol_process_cpu_seconds` would arrive as `otelcol_process_cpu`;
`Matched without unit` reads 0 at every level, and the names arrive exactly as
the table spells them. The author also expected no undocumented `otelcol_*`
name below `detailed`, and three appear at `basic`.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` against the page at commit
`fb0a807365b1`; both move, and the comparison is only valid for that pair. The
absences are properties of this configuration as much as of the level: a
pipeline with `sending_queue` enabled, a scraping receiver, or a processor
built on `processorhelper` would publish names that are missing here. Nothing
is said about the series count, only about the metric names: `detailed`
"includes dimensions and views", and a name that exists at two levels can
carry many more label combinations at the higher one, which is the cost the
page warns about and this question does not measure. The histogram families
are counted once each, so the `_bucket`, `_sum` and `_count` series they
expand into are not in `Metric families published`.
""",
)
