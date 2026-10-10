"""L4-03: hard rename versus dual-write for http.status_code migration."""

import hashlib
import http.server
import socketserver
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import (
    query_value,
    wait_for_url,
)
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = Path("questions/L4-03-semconv-migration")
CONFIG = DIRECTORY / "config"
SERVED = DIRECTORY / "schemas" / "served"
SCHEMA_PORT = 28080
SCHEMA_BASE = f"http://host.docker.internal:{SCHEMA_PORT}"
OLD_SCHEMA = f"{SCHEMA_BASE}/1.20.0"
NEW_SCHEMA = f"{SCHEMA_BASE}/1.21.0"
SERVICE = {"service.name": "l4-03"}
SPANS = 100
BOTH_COUNT = 10
OLD_KEY = "http.status_code"
NEW_KEY = "http.response.status_code"
BATCH_BYTES = (
    '{__name__=~"otelcol_processor_batch_batch_send_size_bytes_sum", job="collector"}'
)
TRANSLATED = (
    '{__name__=~"otelcol_processor_schema_translated(_total)?", job="collector"}'
)
SETTLE = 5.0


class _SchemaHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(SERVED.resolve()), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_schema_server() -> tuple[socketserver.TCPServer, threading.Thread]:
    socketserver.TCPServer.allow_reuse_address = True
    last_error: Exception | None = None
    for _ in range(10):
        try:
            server = socketserver.TCPServer(("127.0.0.1", SCHEMA_PORT), _SchemaHandler)
            break
        except OSError as error:
            last_error = error
            time.sleep(0.3)
    else:
        raise RuntimeError(f"schema server could not bind :{SCHEMA_PORT}: {last_error}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def span_ids(index: int) -> tuple[str, str]:
    digest = hashlib.sha256(f"l4-03/{index}".encode()).hexdigest()
    return digest[:32], digest[32:48]


def make_span(index: int, both: bool) -> dict[str, Any]:
    trace_id, span_id = span_ids(index)
    attrs: dict[str, Any] = {
        "event.sequence": index,
        "phase": "live" if index >= SPANS else "historical",
        OLD_KEY: 200 + (index % 5),
    }
    if both:
        attrs[NEW_KEY] = 500 + (index % 5)
    now = otlp.now_unix_nano()
    return otlp.span(
        f"GET /item/{index}",
        trace_id=trace_id,
        span_id=span_id,
        start_unix_nano=now,
        end_unix_nano=now + 1_000_000,
        span_attributes=attrs,
    )


def historical_spans() -> list[dict[str, Any]]:
    return [make_span(index, both=False) for index in range(SPANS)]


def live_spans() -> list[dict[str, Any]]:
    """Same ids as historical; the last BOTH_COUNT already carry both names."""
    rows = []
    for index in range(SPANS):
        both = index >= SPANS - BOTH_COUNT
        span = make_span(index, both=both)
        span["attributes"] = [
            item
            if item["key"] != "phase"
            else {"key": "phase", "value": otlp.any_value("live")}
            for item in span["attributes"]
        ]
        rows.append(span)
    return rows


def variant(name: str, description: str, config: str) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"./{CONFIG / config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
    )


VARIANTS = (
    variant(
        "none",
        "pass-through Collector, no schema processor",
        "collector-passthrough.yaml",
    ),
    variant(
        "hard-rename",
        "schema target 1.21.0, hard rename",
        "collector-hard-rename.yaml",
    ),
    variant(
        "dual-write",
        "schema target 1.21.0 with migration.from 1.20.0",
        "collector-dual-write.yaml",
    ),
)


def environment(config: str) -> dict[str, str]:
    return stack.environment(
        collector_config=f"./{CONFIG / config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
    )


def recreate_collector(env: dict[str, str]) -> None:
    stack.compose("stop", "collector", env=env)
    stack.compose("rm", "-f", "collector", env=env)
    stack.compose("up", "--detach", "collector", env=env)
    wait_for_url(stack.COLLECTOR_HEALTH_URL)


def printed_span_count(sink_log: str) -> int:
    """Count Span headers. Shared ids across phases must not collapse."""
    return len(debuglog.SPAN_BLOCK.findall(debuglog.unprefixed(sink_log)))


def wait_spans(env: dict[str, str], expected: int) -> int:
    deadline = time.monotonic() + 60.0
    printed = 0
    while time.monotonic() < deadline:
        printed = printed_span_count(stack.logs("sink", env))
        if printed >= expected:
            return printed
        time.sleep(0.5)
    return printed


def query_coverage(attributes: list[dict[str, str]]) -> dict[str, int]:
    old_only = new_only = either = both = 0
    for attrs in attributes:
        has_old = OLD_KEY in attrs
        has_new = NEW_KEY in attrs
        if has_old and not has_new:
            old_only += 1
        if has_new and not has_old:
            new_only += 1
        if has_old or has_new:
            either += 1
        if has_old and has_new:
            both += 1
    return {
        "old_only": old_only,
        "new_only": new_only,
        "either": either,
        "both": both,
    }


def live_attribute_stats(attributes: list[dict[str, str]]) -> dict[str, Any]:
    live = [attrs for attrs in attributes if attrs.get("phase") == "live"]
    if not live:
        return {
            "live_spans": 0,
            "attrs_per_live_span": None,
            "both_kept": 0,
            "live_old_only": 0,
            "live_new_only": 0,
            "live_both": 0,
        }
    coverage = query_coverage(live)
    return {
        "live_spans": len(live),
        "attrs_per_live_span": round(sum(len(attrs) for attrs in live) / len(live), 2),
        "both_kept": coverage["both"],
        "live_old_only": coverage["old_only"],
        "live_new_only": coverage["new_only"],
        "live_both": coverage["both"],
    }


def schema_metrics() -> dict[str, float | None]:
    return {
        "translated": query_value(stack.PROMETHEUS_URL, f"sum({TRANSLATED})"),
        "batch_item_bytes": query_value(stack.PROMETHEUS_URL, f"sum({BATCH_BYTES})"),
    }


def send(variant: Variant) -> dict[str, Any]:
    """History on pass-through, then recreate the Collector into the strategy.

    The runner has already started the stack with the strategy config. This
    switches to pass-through for the historical batch, then back to the
    variant config for the live batch, while the sink log keeps both phases.
    """
    server, _thread = start_schema_server()
    history_env = environment("collector-passthrough.yaml")
    live_env = environment(Path(variant.collector_config).name)
    try:
        recreate_collector(history_env)
        history = otlp.trace_payload(historical_spans(), SERVICE, schema_url=OLD_SCHEMA)
        history_result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "traces", history)
        historical_delivered = wait_spans(history_env, SPANS)
        recreate_collector(live_env)
        time.sleep(SETTLE)
        live = otlp.trace_payload(live_spans(), SERVICE, schema_url=OLD_SCHEMA)
        live_result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "traces", live)
        total_delivered = wait_spans(live_env, SPANS * 2)
        time.sleep(SETTLE)
        sink_log = stack.logs("sink", live_env)
        attrs = debuglog.span_attributes(sink_log)
        coverage = query_coverage(attrs)
        live_stats = live_attribute_stats(attrs)
        schemas = debuglog.resource_schema_urls(sink_log)
        metrics = schema_metrics()
        state = stack.container_state("collector", live_env)
        return {
            "history_http_status": history_result["http_status"],
            "live_http_status": live_result["http_status"],
            "history_request_bytes": history_result["request_bytes"],
            "live_request_bytes": live_result["request_bytes"],
            "historical_delivered": historical_delivered,
            "total_delivered": total_delivered,
            "coverage_old_only": coverage["old_only"],
            "coverage_new_only": coverage["new_only"],
            "coverage_either": coverage["either"],
            "coverage_both": coverage["both"],
            **live_stats,
            "resource_schema_urls": sorted({value for value in schemas if value}),
            "translated": metrics["translated"],
            "batch_item_bytes": metrics["batch_item_bytes"],
            "collector_running": state["running"],
            "collector_oom": state["oom_killed"],
            "collector_restarts": state["restarts"],
            "schema_fetch_host": "host.docker.internal",
        }
    finally:
        server.shutdown()
        server.server_close()
        time.sleep(0.2)


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    traces = debuglog.spans_by_trace(sink_log)
    return {
        "unique_traces": len(traces),
        "printed_spans": printed_span_count(sink_log),
        "resource_schema_urls": sorted(
            {value for value in debuglog.resource_schema_urls(sink_log) if value}
        ),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes: list[str] = []
    if sending.get("historical_delivered") != SPANS:
        notes.append(
            f"historical delivered {sending.get('historical_delivered')} spans"
        )
    if sending.get("total_delivered") != SPANS * 2:
        notes.append(f"total delivered {sending.get('total_delivered')} spans")
    if measured.get("printed_spans") != SPANS * 2:
        notes.append(f"sink printed {measured.get('printed_spans')} spans")
    if measured.get("unique_traces") != SPANS:
        notes.append(
            f"expected {SPANS} unique traces, got {measured.get('unique_traces')}"
        )
    if sending.get("coverage_either") != SPANS * 2:
        notes.append(f"either-query matched {sending.get('coverage_either')}")
    if variant.name == "none":
        if sending.get("coverage_new_only"):
            notes.append("none wrote the new attribute name")
        if sending.get("coverage_both") != BOTH_COUNT:
            notes.append(
                "none should keep both names only on the "
                f"{BOTH_COUNT} spans that arrived with both"
            )
        if sending.get("coverage_old_only") != SPANS * 2 - BOTH_COUNT:
            notes.append(f"none old-only spans were {sending.get('coverage_old_only')}")
    if variant.name == "hard-rename":
        if sending.get("live_new_only") != SPANS:
            notes.append(
                f"hard-rename new-only live spans were {sending.get('live_new_only')}"
            )
        if sending.get("both_kept"):
            notes.append(
                f"hard-rename kept both names on {sending.get('both_kept')} spans"
            )
    if variant.name == "dual-write" and sending.get("live_both") != SPANS:
        notes.append(
            f"dual-write live spans with both names were {sending.get('live_both')}"
        )
    if not sending.get("collector_running"):
        notes.append("the collector is not running")
    if sending.get("collector_oom"):
        notes.append("the collector was OOM-killed")
    if "opentelemetry.io" in str(sending.get("resource_schema_urls")):
        notes.append("a public schema URL reached the sink")
    return notes


QUESTION = Question(
    id="L4-03",
    title="How do you rename an attribute without losing the history behind it?",
    level=4,
    theme="semconv",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=3,
    rotate_variants=True,
    columns=(
        Column("Historical", "sending.historical_delivered"),
        Column("Total delivered", "sending.total_delivered"),
        Column("Old-only", "sending.coverage_old_only"),
        Column("New-only", "sending.coverage_new_only"),
        Column("Old or new", "sending.coverage_either"),
        Column("Both names", "sending.coverage_both"),
        Column("Live attrs/span", "sending.attrs_per_live_span", digits=2),
        Column("Both kept", "sending.both_kept"),
        Column("Translated", "sending.translated", digits=0),
        Column("Batch item B", "sending.batch_item_bytes"),
        Column("Schema URLs", "sending.resource_schema_urls", kind="text"),
        Column("Unique traces", "measured.unique_traces"),
    ),
    interpretation="""
The history stayed in the sink log. Every strategy left the 100 historical
spans on `http.status_code` alone. What changed was only the live batch.

Pass-through never invented the new name. Hard rename moved every live span
onto `http.response.status_code` only, including the ten that already carried
both names, so an old-only query lost the live half of the traffic. Dual-write
put both names on every live span and raised live attributes per span from
3.0 to 4.0; batch item bytes rose from 14336 to 16838 against 13734 with no
processor. An `old OR new` query matched all 200 spans in every strategy.

The practical sequence the numbers support is dual-write while queries move,
then hard rename once the old name is unused. Leaving migration on costs one
extra attribute per renamed key on every live span. History that already
landed under the old name is not rewritten by recreating the Collector; only
a backfill would change it.
""",
    does_not_say="""
History is the sink's append-only debug log, not a trace store with its own
index. The `processor_schema_translated` self-metric was not present under
the scraped name in these runs, so translation counts come from the sink
attributes, not from that counter. Schemas were served locally from vendored
files; a Collector that must fetch `https://opentelemetry.io/schemas/...` on
the public internet is untested here.
""",
)
