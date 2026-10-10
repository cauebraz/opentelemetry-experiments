"""L2-06: the Python SDK exporting to a Collector that is stopped and started."""

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry_experiments.core import debuglog, stack
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L2-06-sdk-without-collector"
EMITTER = stack.ROOT / "questions" / "L2-06-sdk-without-collector" / "emitter.py"
EMITTER_TIMEOUT = 180.0

EXPORT_FAILED = "Failed to export spans batch"
QUEUE_FULL = "Queue full, dropping"


def variant(
    name: str, description: str, processor: str = "batch", queue: int | None = None
) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{DIRECTORY}/config/collector.yaml",
        sink_config=stack.SINK_DETAILED_CONFIG,
        options={"processor": processor, "queue": queue},
    )


VARIANTS = (
    variant("collector-up", "batch processor at its defaults, Collector running"),
    variant(
        "simple-down",
        "SimpleSpanProcessor, 3 spans with the Collector stopped and 3 after",
        processor="simple",
    ),
    variant(
        "batch-short-outage",
        "batch defaults, 100 spans while stopped, Collector started at once",
    ),
    variant(
        "batch-long-outage",
        "batch defaults, 50 spans before a 20s outage and 50 after it",
    ),
    variant(
        "small-queue",
        "queue and batch size 50, 200 spans while stopped, started at once",
        queue=50,
    ),
)


def send(variant: Variant) -> dict[str, Any]:
    environment = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
    )
    options = {
        "scenario": variant.name,
        "environment": environment,
        **variant.options,
    }
    completed = subprocess.run(
        [sys.executable, str(EMITTER), json.dumps(options)],
        cwd=stack.ROOT,
        env={**os.environ, "PYTHONPATH": str(stack.ROOT)},
        capture_output=True,
        text=True,
        timeout=EMITTER_TIMEOUT,
        check=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    return summarize_emitter(result)


def slowest(spans: Sequence[Mapping[str, Any]], phase: str) -> float | None:
    values = [span["end_ms"] for span in spans if span["phase"] == phase]
    return max(values) if values else None


def summarize_emitter(result: Mapping[str, Any]) -> dict[str, Any]:
    spans = result["spans"]
    log = result["sdk_log"]
    return {
        "spans": spans,
        "spans_emitted": len(spans),
        "spans_down": sum(1 for span in spans if span["phase"] == "down"),
        "end_ms_max_down": slowest(spans, "down"),
        "end_ms_max_up": slowest(spans, "up"),
        "exceptions": len(result["exceptions"]),
        "collector_ready_seconds": result["collector_ready_seconds"],
        "flushed": result["flushed"],
        "emitter_seconds": result["seconds"],
        "sdk_warnings": sum(1 for record in log if record["level"] == "warning"),
        "sdk_errors": sum(1 for record in log if record["level"] == "error"),
        "export_failed_logs": sum(
            1 for record in log if EXPORT_FAILED in record["message"]
        ),
        "queue_full_logs": sum(1 for record in log if QUEUE_FULL in record["message"]),
        "sdk_messages": sorted({record["message"] for record in log}),
    }


def ranges(indices: Sequence[int]) -> str:
    """Sorted indices as `a-b` runs, so `0-49,150-199` reads at a glance."""
    ordered = sorted(indices)
    if not ordered:
        return "none"
    runs = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index != previous + 1:
            runs.append((start, previous))
            start = index
        previous = index
    runs.append((start, previous))
    return ",".join(f"{a}-{b}" if a != b else f"{a}" for a, b in runs)


def accounting(
    spans: Sequence[Mapping[str, Any]], arrived: Mapping[str, Any]
) -> dict[str, Any]:
    by_trace = {span["trace_id"]: span for span in spans}
    delivered = [by_trace[trace] for trace in arrived if trace in by_trace]
    seen = {span["index"] for span in delivered}
    return {
        "delivered": len(seen),
        "lost_down": sum(
            1 for span in spans if span["phase"] == "down" and span["index"] not in seen
        ),
        "lost_up": sum(
            1 for span in spans if span["phase"] == "up" and span["index"] not in seen
        ),
        "delivered_ranges": ranges(sorted(seen)),
        "unknown_traces": sum(1 for trace in arrived if trace not in by_trace),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    arrived = debuglog.spans_by_trace(sink_log)
    return {
        **accounting(sending["spans"], arrived),
        "spans_printed": sum(len(spans) for spans in arrived.values()),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes = []
    restarted = sending["spans_down"] > 0
    if restarted and sending["collector_ready_seconds"] is None:
        notes.append("the Collector was stopped but never reported healthy again")
    accepted = (
        run["counters"].get("sink", {}).get("traces", {}).get("receiver_accepted")
    )
    if accepted != measured["spans_printed"]:
        notes.append(
            f"the sink counted {accepted} spans but printed {measured['spans_printed']}"
        )
    if measured["unknown_traces"]:
        notes.append(
            f"the sink printed {measured['unknown_traces']} traces never emitted"
        )
    return notes


QUESTION = Question(
    id="L2-06",
    title="What does the SDK do while there is no Collector to talk to?",
    level=2,
    theme="sdk",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("Spans emitted", "sending.spans_emitted"),
        Column("Emitted while down", "sending.spans_down"),
        Column("Slowest end() down ms", "sending.end_ms_max_down", digits=2),
        Column("Slowest end() up ms", "sending.end_ms_max_up", digits=2),
        Column("Exceptions", "sending.exceptions"),
        Column("Collector ready s", "sending.collector_ready_seconds", digits=2),
        Column("Sink accepted", "counters.sink.traces.receiver_accepted"),
        Column("Delivered", "measured.delivered"),
        Column("Lost while down", "measured.lost_down"),
        Column("Lost after", "measured.lost_up"),
        Column("Delivered ranges", "measured.delivered_ranges", kind="text"),
        Column("SDK warnings", "sending.sdk_warnings"),
        Column("SDK errors", "sending.sdk_errors"),
        Column("Export failed logs", "sending.export_failed_logs"),
        Column("Queue full logs", "sending.queue_full_logs"),
        Column("Emitter seconds", "sending.emitter_seconds", digits=1),
    ),
    interpretation="""
The application was never told. No exception reached the caller in any of the
15 runs, including the ones that lost every span emitted during the outage.
What changed was the SDK's own log, and, with the simple processor, how long the
application thread waited.

`SimpleSpanProcessor` moved the outage into the request path. Each
`span.end()` while the Collector was stopped blocked for 7.55 to 7.70 seconds,
the exporter's backoff of about 1, 2 and 4 seconds before the next 8 second step
no longer fit in its 10 second budget. The span was lost anyway, with one
`Failed to export spans batch due to timeout, max retries or shutdown.` error
per span. After the Collector was back, `span.end()` took 1.95 to 3.04 ms and
all 3 spans arrived.

The batch processor kept `span.end()` under 0.3 ms in every run, and survived a
short outage completely. With the Collector started right after the spans and
healthy 0.42 to 0.43 seconds later, the first scheduled export ran after the 5
second delay against a Collector that was back: 100 of 100 arrived, with no SDK
warning at all. The outage left no trace anywhere.

A 20 second outage lost the batch that was due inside it. The export at 5
seconds retried three times and gave up with one error, and spans 0-49 never
arrived; the 50 spans emitted after recovery all did. One error line stands for
50 lost spans, and it carries no count.

The small queue failed the author's prediction in its detail and confirmed it in
its direction. Only spans 150-199 arrived, in all three runs. The author
expected 0-49 to leave in a first batch and survive on a retry, but the burst of
200 spans filled the 50-slot queue before the worker thread took a batch. The
queue kept the newest 50 and discarded the 150 older ones, and the SDK logged
`Queue full, dropping Span.` once for 150 drops, because its batch logger
filters duplicates.

What this does to an application: use the batch processor, accept that an
outage longer than the exporter's budget loses what was due, and size
`max_queue_size` for the burst the process can produce while an export waits,
because overflow drops the oldest spans silently.
""",
    does_not_say="""
Measured with `opentelemetry-sdk 1.45.0` and
`opentelemetry-exporter-otlp-proto-http 1.45.0` on Python 3.14, traces only, a
Collector container stopped with `docker compose stop` so connections are
refused at once. An unreachable host that drops packets makes each attempt wait
for its timeout instead, and spends the 10 second budget on fewer attempts.

Other language SDKs, the gRPC exporter, the log and metric pipelines of this SDK,
and the exporter's `timeout` and the processor's `schedule_delay_millis` at
anything but their defaults are not tested. The SDK's internal metrics were not
enabled, so this does not say whether they count the drops the log
undercounts.

The small-queue result depends on a burst emitted faster than the worker thread
was scheduled. A slower producer would let the worker take a batch first, and
which spans survive would change.
""",
)
