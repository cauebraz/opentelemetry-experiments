"""L0-01: four versions of one span, three of them wrong on purpose."""

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l0-01"}
SPAN_NAME = "GET /checkout"
DURATION_NANOS = 120_000_000
TRACE_ID = "5b8efff798038103d269b633813fc60c"
SPAN_ID = "eee19b7ec3c1b174"

VARIANTS = (
    Variant(
        name="valid",
        description="a resource, a 16 byte trace id and an end time",
    ),
    Variant(
        name="short-trace-id",
        description="the same span with a 15 byte trace id",
        expects_acceptance=False,
        expects_telemetry=False,
        options={"trace_id": "0" * 30},
    ),
    Variant(
        name="no-resource",
        description="the same span with no resource at all",
        options={"resource": None},
    ),
    Variant(
        name="no-end-time",
        description="the same span with no end time",
        options={"end": False},
    ),
)


def payload_for(variant: Variant) -> dict[str, Any]:
    start = otlp.now_unix_nano()
    end = None if variant.options.get("end") is False else start + DURATION_NANOS
    resource = variant.options.get("resource", SERVICE)
    return otlp.trace_payload(
        [
            otlp.span(
                SPAN_NAME,
                trace_id=variant.options.get("trace_id", TRACE_ID),
                span_id=SPAN_ID,
                start_unix_nano=start,
                end_unix_nano=end,
            )
        ],
        resource,
    )


def send(variant: Variant) -> dict[str, Any]:
    payload = payload_for(variant)
    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "traces", payload)
    return result | {
        "curl": otlp.curl_command(stack.COLLECTOR_HTTP_URL, "traces", payload)
    }


SPAN_FIELD = re.compile(
    r"^\s*(Trace ID|Parent ID|ID|Name|Kind|Start time|End time|Status code)"
    r"\s*:\s*(.*?)\s*$",
    re.MULTILINE,
)
RESOURCE_BLOCK = re.compile(
    r"^Resource attributes:\n((?:\s+-> .*\n)*)",
    re.MULTILINE,
)


ZERO_INSTANT = "1970-01-01 00:00:00 +0000 UTC"
PRINTED_TIME = "%Y-%m-%d %H:%M:%S.%f %z"


def parse_printed_time(text: str | None) -> datetime | None:
    """`debug` prints Go's time format; the trailing zone name is not strptime's."""
    if not text:
        return None
    stamp = text.removesuffix(" UTC")
    if "." not in stamp.split(" ")[1]:
        stamp = stamp.replace(" +0000", ".0 +0000")
    try:
        return datetime.strptime(stamp, PRINTED_TIME)
    except ValueError:
        return None


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    """No wall-clock value is recorded, only what it means, so runs stay comparable."""
    text = stack.unprefixed(collector_log)
    fields = dict(SPAN_FIELD.findall(text))
    resource_blocks = RESOURCE_BLOCK.findall(text)
    end_text = fields.get("End time")
    start = parse_printed_time(fields.get("Start time"))
    end = parse_printed_time(end_text)
    zero_end = end_text == ZERO_INSTANT
    return {
        "debug_spans": len(re.findall(r"^Span #\d+$", text, re.MULTILINE)),
        "resource_attributes": sum(block.count("-> ") for block in resource_blocks),
        "span_name": fields.get("Name"),
        "end_time_is_the_zero_instant": (
            None if end_text is None else ("yes" if zero_end else "no")
        ),
        "duration_ms": (
            None
            if zero_end or start is None or end is None
            else round((end - start).total_seconds() * 1000)
        ),
        "trace_id_hex_length": (
            None if "Trace ID" not in fields else len(fields["Trace ID"])
        ),
    }


QUESTION = Question(
    id="L0-01",
    title="What does the Collector do with a span you send by hand?",
    level=0,
    theme="traces",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Accepted", "counters.collector.traces.receiver_accepted"),
        Column("Refused", "counters.collector.traces.receiver_refused"),
        Column("Spans printed", "measured.debug_spans"),
        Column("Resource attrs", "measured.resource_attributes"),
        Column("Warn lines", "log.warn_lines"),
        Column(
            "End time is 1970", "measured.end_time_is_the_zero_instant", kind="text"
        ),
        Column("Duration ms", "measured.duration_ms"),
        Column("Trace id hex chars", "measured.trace_id_hex_length"),
        Column("Response body", "sending.response_body", kind="text"),
    ),
    interpretation="""
Three of the four spans were accepted. Only one of them was accepted
unchanged.

The 15 byte `trace_id` never became a span. The receiver returned 400 with
`ID.UnmarshalJSONIter: length mismatch` before anything reached a pipeline,
and neither `otelcol_receiver_accepted_spans` nor
`otelcol_receiver_refused_spans` exists in that run, which is why both cells
read `-` rather than `0`. A Collector dashboard built on those counters is
blank, not red, while the sender loses every span it emits. The only place the
failure exists is the sender's own HTTP client.

The span with no resource was accepted and printed with zero resource
attributes. Nothing warned. A backend that groups by `service.name` has to put
that span somewhere, and the Collector has already agreed to forward it.

The span with no end time was accepted, and the Collector resolved the missing
field to the Unix epoch, printing `1970-01-01 00:00:00 +0000 UTC`. Over three
repetitions the collector log carried zero warnings for every variant. A
duration computed from that span is the age of the Unix epoch, which is the
kind of value that survives an average and destroys a percentile.

On a bill, none of this is expensive. On a dashboard, the 400 is the cheap
failure, because someone sees it immediately. The accepted-and-altered spans
are the expensive ones, because they arrive looking like data.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` over OTLP/HTTP with JSON encoding and
one span per request. The protobuf encoding and the gRPC transport have their
own decoders, and gRPC carries `trace_id` as raw bytes, where a length error
is a different kind of error. Nothing here says what happens to a batch in
which only one span is malformed, which is what a real SDK produces. The
`debug` exporter is a reading surface and not a backend: the zero end time is
the Collector's rendering of an absent field, and a vendor may store or
display it differently.
""",
)
