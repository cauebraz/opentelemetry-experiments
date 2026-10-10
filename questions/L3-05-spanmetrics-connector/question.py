"""L3-05: series and accuracy of spanmetrics, bounded, unbounded and sampled."""

import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry_experiments.core import debuglog, stack
from opentelemetry_experiments.core.prometheus import query_result, query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L3-05-spanmetrics-connector"
EMITTER = stack.ROOT / "questions" / "L3-05-spanmetrics-connector" / "emitter.py"
EMITTER_TIMEOUT = 180.0
SETTLE_TIMEOUT = 120.0
QUIET_SAMPLES = 5
MEMORY_CEILING = "512m"
TRACES = 1000
ROUTES = 5
PER_ROUTE = 200
ERRORS_PER_ROUTE = 20
HEAD_RATIO = 0.10
MIB = 1024 * 1024

FAMILIES = {
    "calls": "l3_05_calls_total",
    "bucket": "l3_05_duration_milliseconds_bucket",
    "sum": "l3_05_duration_milliseconds_sum",
    "count": "l3_05_duration_milliseconds_count",
}
ERROR_STATUS = "STATUS_CODE_ERROR"
DECISIONS = (
    '{__name__=~"otelcol_processor_tail_sampling_global_count_traces_sampled'
    '(_total)?", job="collector"}'
)
EARLY_DROPS = (
    '{__name__=~"otelcol_processor_tail_sampling_sampling_trace_dropped_too_early'
    '(_total)?", job="collector"}'
)


def variant(name: str, description: str, config: str, tail: bool) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{DIRECTORY}/config/{config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
        collector_memory_limit=MEMORY_CEILING,
        options={"tail": tail, "head": name.endswith("head-10")},
    )


VARIANTS = (
    variant(
        "bounded-all",
        "dimension http.route, every span kept",
        "collector-bounded.yaml",
        False,
    ),
    variant(
        "unbounded-all",
        "dimensions http.route and customer.id, every span kept",
        "collector-unbounded.yaml",
        False,
    ),
    variant(
        "unbounded-head-10",
        "unbounded dimensions after SDK TraceIdRatioBased(0.10)",
        "collector-unbounded.yaml",
        False,
    ),
    variant(
        "unbounded-tail-errors",
        "unbounded dimensions after tail_sampling status_code ERROR",
        "collector-unbounded-tail-errors.yaml",
        True,
    ),
)


def value(expression: str) -> float | None:
    return query_value(stack.PROMETHEUS_URL, expression)


def selector(family: str, extra: str = "") -> str:
    return f'{{__name__="{FAMILIES[family]}", job="exported"{extra}}}'


def series(family: str) -> int:
    return len(query_result(stack.PROMETHEUS_URL, selector(family)))


def peak_mib(metric: str) -> float | None:
    reading = value(
        f'max(max_over_time({{__name__="{metric}", job="collector"}}[10m]))'
    )
    return None if reading is None else round(reading / MIB, 1)


def wait_until_settled(tail: bool) -> float:
    started = time.monotonic()
    previous = None
    repeats = 0
    while time.monotonic() - started < SETTLE_TIMEOUT:
        calls = value(f"sum({selector('calls')})")
        count = value(f"sum({selector('count')})")
        decided = not tail or (value(f"sum({DECISIONS})") or 0) >= TRACES
        current = (calls, count)
        stable = calls is not None and current == previous
        repeats = repeats + 1 if stable else 0
        if decided and repeats >= QUIET_SAMPLES:
            return round(time.monotonic() - started, 1)
        previous = current
        time.sleep(1.0)
    raise TimeoutError(f"spanmetrics did not settle after {SETTLE_TIMEOUT:g}s")


def readings() -> dict[str, Any]:
    by_route = {
        item["metric"].get("http_route", ""): round(float(item["value"][1]))
        for item in query_result(
            stack.PROMETHEUS_URL, f"sum by (http_route) ({selector('calls')})"
        )
    }
    customers = query_result(
        stack.PROMETHEUS_URL, f"count by (customer_id) ({selector('calls')})"
    )
    p95 = value(f"histogram_quantile(0.95, sum by (le) ({selector('bucket')}))")
    return {
        "series": {family: series(family) for family in FAMILIES},
        "requests": value(f"sum({selector('calls')})"),
        "errors": value(
            "sum(" + selector("calls", f', status_code="{ERROR_STATUS}"') + ")"
        )
        or 0,
        "duration_sum": value(f"sum({selector('sum')})"),
        "duration_count": value(f"sum({selector('count')})"),
        "p95_ms": None if p95 is None else round(p95, 1),
        "calls_by_route": by_route,
        "customers": len([item for item in customers if item["metric"]]),
    }


def send(variant: Variant) -> dict[str, Any]:
    environment = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
        collector_memory_limit=variant.collector_memory_limit,
    )
    completed = subprocess.run(
        [sys.executable, str(EMITTER), json.dumps({"scenario": variant.name})],
        cwd=stack.ROOT,
        env={**os.environ, "PYTHONPATH": str(stack.ROOT)},
        capture_output=True,
        text=True,
        timeout=EMITTER_TIMEOUT,
        check=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    tail = variant.options["tail"]
    settle = wait_until_settled(tail)
    state = stack.container_state("collector", environment)
    return {
        **result,
        **workload(result["spans"]),
        "settle_seconds": settle,
        "metrics": readings(),
        "decisions": value(f"sum({DECISIONS})") if tail else None,
        "dropped_too_early": (value(f"sum({EARLY_DROPS})") or 0) if tail else None,
        "heap_peak_mib": peak_mib("otelcol_process_runtime_heap_alloc_bytes"),
        "running": state["running"],
        "oom_killed": state["oom_killed"],
        "restarts": state["restarts"],
    }


def workload(spans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    routes = Counter(span["route"] for span in spans)
    errors = Counter(span["route"] for span in spans if span["error"])
    return {
        "traces_generated": len(spans),
        "error_traces_generated": sum(errors.values()),
        "sdk_selected": sum(1 for span in spans if span["selected"]),
        "balanced": (
            len(routes) == ROUTES
            and set(routes.values()) == {PER_ROUTE}
            and set(errors.values()) == {ERRORS_PER_ROUTE}
        ),
    }


def percent(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100 * part / whole, 1)


def accuracy(
    metrics: Mapping[str, Any], scale: float, truth: Mapping[str, float]
) -> dict[str, Any]:
    requests = metrics["requests"]
    scaled = None if requests is None else round(requests * scale)
    count = metrics["duration_count"]
    mean = (
        None
        if not count or metrics["duration_sum"] is None
        else round(metrics["duration_sum"] / count, 1)
    )
    rate = percent(metrics["errors"], requests)
    total_series = sum(metrics["series"].values())
    return {
        "combinations": metrics["series"]["calls"],
        "total_series": total_series,
        "series_per_combination": (
            round(total_series / metrics["series"]["calls"], 1)
            if metrics["series"]["calls"]
            else None
        ),
        "series_per_input_request": round(total_series / truth["requests"], 2),
        "requests_scaled": scaled,
        "request_error_pct": (
            None
            if scaled is None
            else round(100 * (scaled - truth["requests"]) / truth["requests"], 1)
        ),
        "error_capture_pct": percent(metrics["errors"], truth["errors"]),
        "error_rate_pct": rate,
        "error_rate_error_pp": (
            None if rate is None else round(rate - truth["error_rate_pct"], 1)
        ),
        "mean_ms": mean,
        "mean_error_pct": (
            None
            if mean is None
            else round(100 * (mean - truth["mean_ms"]) / truth["mean_ms"], 1)
        ),
    }


TRUTH = {"requests": 1000, "errors": 100, "error_rate_pct": 10.0, "mean_ms": 190.0}


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    arrived = debuglog.spans_by_trace(sink_log)
    generated = {span["trace_id"]: span for span in sending["spans"]}
    known = [trace for trace in arrived if trace in generated]
    scale = 1 / HEAD_RATIO if variant.options["head"] else 1.0
    return {
        "delivered_spans": sum(len(arrived[trace]) for trace in known),
        "delivered_errors": sum(1 for trace in known if generated[trace]["error"]),
        "unknown_traces": len(set(arrived) - set(generated)),
        "spans_printed": sum(len(ids) for ids in arrived.values()),
        **accuracy(sending["metrics"], scale, TRUTH),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    metrics = sending["metrics"]
    notes: list[str] = []
    if not sending["flushed"] or not sending["shutdown"] or sending["exceptions"]:
        notes.append("the emitter did not flush, shut down or finish cleanly")
    if sending["traces_generated"] != TRACES or not sending["balanced"]:
        notes.append("the workload is not 1000 traces, 200 and 20 errors per route")
    if measured["unknown_traces"]:
        notes.append(f"the sink printed {measured['unknown_traces']} unknown traces")
    accepted = (
        run["counters"].get("sink", {}).get("traces", {}).get("receiver_accepted")
    )
    if accepted != measured["spans_printed"]:
        notes.append(
            f"the sink counted {accepted} spans but printed {measured['spans_printed']}"
        )
    retained = measured["delivered_spans"]
    if metrics["requests"] != retained:
        notes.append(f"calls read {metrics['requests']} for {retained} retained spans")
    if metrics["duration_count"] != retained:
        notes.append(
            f"the histogram counted {metrics['duration_count']} "
            f"for {retained} retained spans"
        )
    if metrics["errors"] != measured["delivered_errors"]:
        notes.append(
            f"error calls read {metrics['errors']} "
            f"for {measured['delivered_errors']} retained errors"
        )
    missing = [family for family, count in metrics["series"].items() if not count]
    if missing:
        notes.append(f"families missing: {', '.join(missing)}")
    if variant.options["tail"]:
        if sending["decisions"] != TRACES:
            notes.append(f"tail_sampling decided {sending['decisions']} traces")
        if sending["dropped_too_early"]:
            notes.append(f"tail_sampling dropped {sending['dropped_too_early']} early")
    if not sending["running"] or sending["oom_killed"] or sending["restarts"]:
        notes.append("the collector did not survive the run")
    return notes


QUESTION = Question(
    id="L3-05",
    title="What does deriving RED metrics from spans cost in series?",
    level=3,
    theme="traces, metrics",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("Traces generated", "sending.traces_generated"),
        Column("SDK selected", "sending.sdk_selected"),
        Column("Retained spans", "measured.delivered_spans"),
        Column("Combinations", "measured.combinations"),
        Column("Calls series", "sending.metrics.series.calls"),
        Column("Bucket series", "sending.metrics.series.bucket"),
        Column("Sum series", "sending.metrics.series.sum"),
        Column("Count series", "sending.metrics.series.count"),
        Column("Total series", "measured.total_series"),
        Column("Series per combination", "measured.series_per_combination", digits=1),
        Column("Series per request", "measured.series_per_input_request", digits=2),
        Column("Customers", "sending.metrics.customers"),
        Column("Requests", "sending.metrics.requests"),
        Column("Requests scaled", "measured.requests_scaled"),
        Column("Request error %", "measured.request_error_pct", digits=1),
        Column("Errors", "sending.metrics.errors"),
        Column("Error capture %", "measured.error_capture_pct", digits=1),
        Column("Error rate %", "measured.error_rate_pct", digits=1),
        Column("Mean ms", "measured.mean_ms", digits=1),
        Column("Mean error %", "measured.mean_error_pct", digits=1),
        Column("p95 ms", "sending.metrics.p95_ms", digits=1),
        Column("Tail decisions", "sending.decisions"),
        Column("Dropped too early", "sending.dropped_too_early"),
        Column("Heap peak MiB", "sending.heap_peak_mib", digits=1),
        Column("Settle s", "sending.settle_seconds", digits=1),
    ),
    interpretation="""
One combination of dimensions cost exactly 10 series in every run: 1 calls
series and 9 for the duration histogram, 7 buckets plus sum and count. The
bounded route dimension made 10 combinations and 100 series. Adding a
unique-per-request `customer.id` made 1000 combinations and 10000 series, 10
series per request, while the requests, the 100 errors and the 190.0 ms mean
stayed exact.

The unbounded dimension also cost the Collector memory. Peak heap on the one
second scrape was 30.6 to 33.6 MiB with the route alone and 49.9 to 57.8 MiB
with `customer.id`, for 1000 spans.

Head sampling cut the series in proportion and bent every RED number. The SDK
kept 103 traces, which made 103 combinations and 1030 series. Scaled by 10,
calls read 1030, 3.0% high. The errors kept were 6 of 100, so the error rate
read 5.8% against 10.0%, and the mean duration read 152.4 ms, 19.8% low.

The error-only tail policy cost 1000 series, the same order as the head
sampler, and described none of the successful traffic: 100 calls, an error rate
of 100%, and a mean of 1000.0 ms.

The histogram misread the percentile with no sampling at all. The true p95 is
1000 ms, since 10% of requests take 1000 ms. `histogram_quantile` interpolates
inside the `500ms` to `1s` bucket and returned 750.0 ms whenever every span was
kept. It returned 570.8 ms after head sampling and 975.0 ms after the error-only
policy.

What this does to a bill: a span attribute promoted to a `spanmetrics`
dimension costs 10 series per distinct value, so a per-request value turns 1000
requests into 10000 series. Sampling upstream lowers that bill by changing what
the metrics say. Derive RED metrics before sampling, on bounded dimensions only.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with one service, single-span traces,
1000 requests in one burst, explicit buckets of `50ms, 100ms, 250ms, 500ms, 1s,
2s`, cumulative temporality and a fresh Prometheus. A customer that returns
reuses its series. The count here is the worst case of an id seen once, and it
says nothing about churn, `metrics_expiration` or retention.

`aggregation_cardinality_limit`, exponential histograms, `exemplars` and
deriving metrics before sampling were not run. The head sample is one draw of
fixed ids, not a distribution. The heap peaks come from a one second scrape
and 1000 small spans.
""",
)
