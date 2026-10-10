"""Skeleton for a question. Delete every comment that does not survive the edit."""

from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.runner import Column, Question, Variant

VARIANTS = (
    Variant(
        name="variant-name",
        description="what this variant sends, in one clause",
        collector_config="./questions/L0-00-slug/config/collector.yaml",
        options={},
    ),
)


def send(variant: Variant) -> dict[str, Any]:
    """Put the payload on the wire and return exactly what came back."""
    payload = otlp.trace_payload([otlp.span("example")])
    return otlp.post_json(stack.COLLECTOR_HTTP_URL, "traces", payload)


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    """Anything the counters do not carry, read from the log or a query."""
    return {"debug_blocks": collector_log.count("ResourceSpans #0")}


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    """Integrity checks that are not columns; each sentence returned invalidates."""
    return []


QUESTION = Question(
    id="L0-00",
    title="One sentence, in the form of a question",
    level=0,
    theme="collector",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Accepted spans", "counters.collector.traces.receiver_accepted"),
        Column("Response", "sending.response_body", kind="text"),
    ),
    interpretation="""
Written after the run. What the numbers mean, and one sentence on what this
does to a dashboard, an alert or a bill.
""",
    does_not_say="""
Written after the run. The limits of the measurement.
""",
)
