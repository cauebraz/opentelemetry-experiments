"""L3-03: head sampling in the SDK against tail sampling in the Collector."""

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import debuglog, stack
from opentelemetry_experiments.core.prometheus import query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L3-03-head-vs-tail-sampling"
EMITTER = stack.ROOT / "questions" / "L3-03-head-vs-tail-sampling" / "emitter.py"
EMITTER_TIMEOUT = 180.0
MEMORY_CEILING = "512m"
DRAIN_TIMEOUT = 120.0
QUIET_SAMPLES = 5
MIB = 1024 * 1024

DECISIONS = (
    '{__name__=~"otelcol_processor_tail_sampling_global_count_traces_sampled'
    '(_total)?", job="collector"}'
)
EARLY_DROPS = (
    '{__name__=~"otelcol_processor_tail_sampling_sampling_trace_dropped_too_early'
    '(_total)?", job="collector"}'
)
SINK_SPANS = '{__name__=~"otelcol_receiver_accepted_spans(_total)?", job="sink"}'


def variant(name: str, description: str, config: str, tail: bool) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{DIRECTORY}/config/{config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
        collector_memory_limit=MEMORY_CEILING,
        options={"tail": tail},
    )


VARIANTS = (
    variant(
        "all", "SDK keeps everything, no Collector sampling", "collector.yaml", False
    ),
    variant(
        "head-10",
        "SDK ParentBased(TraceIdRatioBased(0.10)), no Collector sampling",
        "collector.yaml",
        False,
    ),
    variant(
        "tail-probabilistic-10",
        "tail_sampling probabilistic 10%",
        "collector-tail-probabilistic.yaml",
        True,
    ),
    variant(
        "tail-errors",
        "tail_sampling status_code ERROR",
        "collector-tail-errors.yaml",
        True,
    ),
    variant(
        "tail-errors-plus-10",
        "tail_sampling status_code ERROR or probabilistic 10%",
        "collector-tail-errors-plus-10.yaml",
        True,
    ),
)


def collector_value(expression: str) -> float | None:
    return query_value(stack.PROMETHEUS_URL, expression)


def peak_mib(metric: str) -> float | None:
    value = collector_value(
        f'max(max_over_time({{__name__="{metric}", job="collector"}}[10m]))'
    )
    return None if value is None else round(value / MIB, 1)


def wait_for_drain(tail: bool, expected: int, started: float) -> float | None:
    """Seconds to the last change at the sink, once every trace is decided."""
    previous = None
    last_change = None
    repeats = 0
    while time.monotonic() - started < DRAIN_TIMEOUT:
        current = collector_value(f"sum({SINK_SPANS})")
        if current is None:
            time.sleep(1.0)
            continue
        if current != previous:
            last_change = time.monotonic()
            repeats = 0
        else:
            repeats += 1
        decided = not tail or (collector_value(f"sum({DECISIONS})") or 0) >= expected
        if decided and repeats >= QUIET_SAMPLES:
            return round(last_change - started, 1)
        previous = current
        time.sleep(1.0)
    raise TimeoutError(f"the sink did not settle after {DRAIN_TIMEOUT:g}s")


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
    flushed_at = time.monotonic()
    tail = variant.options["tail"]
    drain = wait_for_drain(tail, len(result["generated"]), flushed_at)
    state = stack.container_state("collector", environment)
    return {
        **result,
        "traces_generated": len(result["generated"]),
        "spans_generated": len(result["generated"]) * result["spans_per_trace"],
        "error_traces_generated": len(result["errors"]),
        "sdk_selected_traces": len(result["selected"]),
        "exception_count": len(result["exceptions"]),
        "drain_seconds": drain,
        "decisions": collector_value(f"sum({DECISIONS})") if tail else None,
        "sampled_decisions": (
            collector_value(f'sum({DECISIONS[:-1]}, sampled="true"}})')
            if tail
            else None
        ),
        "dropped_too_early": (
            collector_value(f"sum({EARLY_DROPS})") or 0 if tail else None
        ),
        "heap_peak_mib": peak_mib("otelcol_process_runtime_heap_alloc_bytes"),
        "rss_peak_mib": peak_mib("otelcol_process_memory_rss"),
        "running": state["running"],
        "oom_killed": state["oom_killed"],
        "restarts": state["restarts"],
    }


def percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def accounting(
    sending: Mapping[str, Any], arrived: Mapping[str, set[str]]
) -> dict[str, Any]:
    generated = set(sending["generated"])
    errors = set(sending["errors"])
    size = sending["spans_per_trace"]
    known = {trace: ids for trace, ids in arrived.items() if trace in generated}
    delivered_spans = sum(len(ids) for ids in known.values())
    error_delivered = sum(1 for trace in known if trace in errors)
    total = len(sending["generated"])
    return {
        "delivered_traces": len(known),
        "delivered_spans": delivered_spans,
        "complete_traces": sum(1 for ids in known.values() if len(ids) == size),
        "incomplete_traces": sum(1 for ids in known.values() if len(ids) != size),
        "error_traces_delivered": error_delivered,
        "success_traces_delivered": len(known) - error_delivered,
        "unknown_traces": len(set(arrived) - generated),
        "error_capture_pct": percent(error_delivered, len(errors)),
        "retained_pct": percent(delivered_spans, total * size),
        "request_ratio": round(len(known) / total, 3) if total else None,
        "observed_error_rate_pct": percent(error_delivered, len(known)),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    arrived = debuglog.spans_by_trace(sink_log)
    return {
        **accounting(sending, arrived),
        "spans_printed": sum(len(ids) for ids in arrived.values()),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes: list[str] = []
    if not sending["flushed"] or not sending["shutdown"]:
        notes.append("the SDK did not flush or shut down cleanly")
    if sending["exceptions"]:
        notes.append(f"the emitter raised {len(sending['exceptions'])} exceptions")
    if measured["unknown_traces"]:
        notes.append(
            f"the sink printed {measured['unknown_traces']} traces never generated"
        )
    if measured["incomplete_traces"]:
        notes.append(f"{measured['incomplete_traces']} delivered traces are incomplete")
    accepted = (
        run["counters"].get("sink", {}).get("traces", {}).get("receiver_accepted")
    )
    if accepted != measured["spans_printed"]:
        notes.append(
            f"the sink counted {accepted} spans but printed {measured['spans_printed']}"
        )
    unread = [
        name
        for name in ("heap_peak_mib", "rss_peak_mib", "drain_seconds")
        if sending[name] is None
    ]
    if unread:
        notes.append(f"never read: {', '.join(unread)}")
    if variant.options["tail"]:
        expected = len(sending["generated"])
        if sending["decisions"] != expected:
            notes.append(
                f"tail_sampling decided {sending['decisions']} of {expected} traces"
            )
        if sending["dropped_too_early"]:
            notes.append(
                f"tail_sampling dropped {sending['dropped_too_early']} traces early"
            )
    if not sending["running"] or sending["oom_killed"] or sending["restarts"]:
        notes.append(
            "the collector did not survive the run: "
            f"running {sending['running']}, OOM killed {sending['oom_killed']}, "
            f"restarts {sending['restarts']}"
        )
    return notes


QUESTION = Question(
    id="L3-03",
    title="How many traces with errors survive head sampling and tail sampling?",
    level=3,
    theme="traces",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("Traces generated", "sending.traces_generated"),
        Column("Spans generated", "sending.spans_generated"),
        Column("Error traces generated", "sending.error_traces_generated"),
        Column("SDK selected", "sending.sdk_selected_traces"),
        Column(
            "Collector accepted spans", "counters.collector.traces.receiver_accepted"
        ),
        Column("Delivered traces", "measured.delivered_traces"),
        Column("Delivered spans", "measured.delivered_spans"),
        Column("Complete", "measured.complete_traces"),
        Column("Incomplete", "measured.incomplete_traces"),
        Column("Error traces delivered", "measured.error_traces_delivered"),
        Column("Success traces delivered", "measured.success_traces_delivered"),
        Column("Error capture %", "measured.error_capture_pct", digits=1),
        Column("Retained %", "measured.retained_pct", digits=1),
        Column("Request ratio", "measured.request_ratio", digits=3),
        Column("Observed error rate %", "measured.observed_error_rate_pct", digits=1),
        Column("Heap peak MiB", "sending.heap_peak_mib", digits=1),
        Column("RSS peak MiB", "sending.rss_peak_mib", digits=1),
        Column("Tail decisions", "sending.decisions"),
        Column("Tail sampled", "sending.sampled_decisions"),
        Column("Dropped too early", "sending.dropped_too_early"),
        Column("Drain s", "sending.drain_seconds", digits=1),
        Column("OOM killed", "sending.oom_killed", kind="text"),
        Column("Restarts", "sending.restarts"),
        Column("SDK exceptions", "sending.exception_count"),
    ),
    interpretation="""
Head sampling kept 12 of the 100 error traces. `ParentBased(TraceIdRatioBased
(0.10))` selected the same 98 of 1000 traces in all three repetitions, because
the ids are deterministic and so is the sampler, and 12 of those 98 carried the
error. 88 errors were never exported, and nothing downstream can recover them.

Tail sampling on status kept all 100. `status_code: ERROR` delivered exactly the
100 error traces and their 500 spans, 10.0% of the volume, with every trace
complete. The dashboard built on what it stored reads an error rate of 100% and
a request count of 100, against 10% and 1000 in the traffic.

No variant keeps a dashboard honest without correction. `head-10` and
`tail-probabilistic-10` kept 98 and 114 traces, request ratios of 0.098 and
0.114, and observed error rates of 12.2% and 12.3% against the real 10.0%. The
combined policy kept the 100 errors plus 100 of the 900 successes, 1000 spans,
20.0% of the volume, and an observed error rate of 50.0%. An unadjusted count
of received spans measures the sampler, not the traffic.

The Collector paid little for 1000 buffered traces. Peak heap on the one second
scrape was 30.7 to 32.4 MiB under `all` and 32.2 to 36.3 MiB with
`tail_sampling`, peak RSS 202.2 to 208.5 MiB, under the 512m ceiling in every
run. `tail_sampling` decided all 1000 traces in every tail run and dropped none
early.

The drain did not separate the samplers as the author predicted. The last
change at the sink came 5.0 to 6.0 seconds after the flush without
`tail_sampling` and
6.0 to 7.1 seconds with `tail_sampling`, not the 5 seconds longer that
`decision_wait: 5s` suggested. Every variant shows a floor of about 5 seconds
that this question did not isolate.

What this does to a bill: the error-only policy stores 10% of the volume and
keeps every failure; the 10% samplers store the same 10% and lose 86 to 88% of
them. A request count or error rate on sampled spans needs the sampling ratio
applied, and an error-keeping policy has no single ratio to apply.
""",
    does_not_say="""
Measured with `opentelemetry-sdk 1.45.0`, one service, one Collector and
`otelcol-contrib 0.162.0`, 1000 traces of 5 spans emitted in a burst under
0.05 seconds, with the error on the third child of every tenth trace. The trace
ids are fixed, so the 98 and 114 selected traces are one draw of each hash, not
a distribution; other ids would select other counts around 100.

Traces whose spans come from several services, arrive over more than
`decision_wait`, or are spread across several Collectors are not tested. Late
spans, the decision cache and `span-ingest` are not exercised. Memory comes
from a one second scrape and 5000 small spans; it says nothing about
`num_traces` at production rates. The SDK queue was raised to 8192 because the
default of 2048 dropped half of the burst before export; at the default, the
SDK loses spans before any sampler sees them. No vendor price is applied, so
storage is relative volume, not cost.
""",
)
