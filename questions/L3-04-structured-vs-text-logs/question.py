"""L3-04: the same events as parsed text, drifting text and typed attributes."""

import hashlib
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import query_result, query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

CONFIG = "./questions/L3-04-structured-vs-text-logs/config/collector.yaml"
SERVICE = {"service.name": "l3-04"}
EVENTS = 1000
ROUTES = ("/checkout", "/cart", "/search", "/login", "/profile")
DRIFTED = frozenset({*(block * 100 + 27 for block in range(9)), 950})
COUNT_DIMENSIONS = ("http.route", "outcome")
UNPARSED = "unparsed"
PARSER = re.compile(r"route=(?P<route>\S+) outcome=(?P<outcome>\S+)")
EXPORT_TIMEOUT = 60.0

RECORDS = '{__name__=~"l3_04_records(_total)?", job="exported"}'
REQUESTS = '{__name__=~"l3_04_requests(_total)?", job="exported"}'
BATCH_BYTES = (
    '{__name__=~"otelcol_processor_batch_batch_send_size_bytes_sum", job="collector"}'
)


@dataclass(frozen=True)
class Event:
    index: int
    route: str
    outcome: str
    duration_ms: int
    request_id: str


def events() -> list[Event]:
    return [
        Event(
            index=index,
            route=ROUTES[index % len(ROUTES)],
            outcome="error" if (index // len(ROUTES)) % 10 == 0 else "ok",
            duration_ms=100 + index % 50,
            request_id=hashlib.sha256(f"l3-04/{index}".encode()).hexdigest()[:16],
        )
        for index in range(EVENTS)
    ]


def truth() -> Counter[tuple[str, str]]:
    return Counter((event.route, event.outcome) for event in events())


def text_body(event: Event, drift: bool) -> str:
    if drift:
        return (
            f"{event.request_id} {event.outcome} took {event.duration_ms}ms "
            f"on {event.route}"
        )
    return (
        f"request completed route={event.route} outcome={event.outcome} "
        f"duration_ms={event.duration_ms} request_id={event.request_id}"
    )


def record(event: Event, shape: str, now: int) -> dict[str, Any]:
    if shape == "structured":
        return otlp.log_record(
            "request completed",
            time_unix_nano=now,
            record_attributes={
                "event.sequence": event.index,
                "http.route": event.route,
                "outcome": event.outcome,
                "duration_ms": event.duration_ms,
                "request_id": event.request_id,
            },
        )
    drift = shape == "text-drift-1pct" and event.index in DRIFTED
    return otlp.log_record(
        text_body(event, drift),
        time_unix_nano=now,
        record_attributes={"event.sequence": event.index},
    )


def records(shape: str) -> list[dict[str, Any]]:
    now = otlp.now_unix_nano()
    return [record(event, shape, now) for event in events()]


VARIANTS = tuple(
    Variant(
        name=name,
        description=description,
        collector_config=CONFIG,
        sink_config=stack.SINK_DETAILED_CONFIG,
    )
    for name, description in (
        ("text-canonical", "every field in the body, parsed by the Collector"),
        ("text-drift-1pct", "the same text with 10 records in a second format"),
        ("structured", "a short body and typed attributes, nothing to parse"),
    )
)


def collector_value(expression: str) -> float | None:
    return query_value(stack.PROMETHEUS_URL, expression)


def categories() -> dict[tuple[str, str], int]:
    return {
        (
            item["metric"].get("http_route", UNPARSED),
            item["metric"].get("outcome", UNPARSED),
        ): round(float(item["value"][1]))
        for item in query_result(stack.PROMETHEUS_URL, REQUESTS)
    }


def wait_for_counts(expected: int) -> float | None:
    deadline = time.monotonic() + EXPORT_TIMEOUT
    control = None
    while time.monotonic() < deadline:
        control = collector_value(f"sum({RECORDS})")
        requested = collector_value(f"sum({REQUESTS})")
        batched = collector_value(f"sum({BATCH_BYTES})")
        if control == expected and requested == expected and batched:
            return control
        time.sleep(1.0)
    return control


def miscounted(
    observed: Mapping[tuple[str, str], int], expected: Mapping[tuple[str, str], int]
) -> int:
    """Records counted under the wrong category, each counted once."""
    keys = set(observed) | set(expected)
    return sum(max(0, expected.get(key, 0) - observed.get(key, 0)) for key in keys)


def send(variant: Variant) -> dict[str, Any]:
    payload = otlp.log_payload(records(variant.name), SERVICE)
    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "logs", payload)
    control = wait_for_counts(EVENTS)
    observed = categories()
    unparsed = sum(count for (route, _), count in observed.items() if route == UNPARSED)
    batch_bytes = collector_value(f"sum({BATCH_BYTES})")
    return {
        "http_status": result["http_status"],
        "records_sent": EVENTS,
        "request_bytes": result["request_bytes"],
        "request_bytes_per_record": round(result["request_bytes"] / EVENTS, 1),
        "batch_item_bytes": batch_bytes,
        "batch_item_bytes_per_record": (
            None if batch_bytes is None else round(batch_bytes / EVENTS, 1)
        ),
        "control_count": control,
        "categorized_count": sum(observed.values()) - unparsed,
        "unparsed_count": unparsed,
        "request_series": len(observed),
        "miscounted": miscounted(observed, truth()),
        "categories": {
            f"{route} {outcome}": n for (route, outcome), n in observed.items()
        },
    }


def accounting(printed: Sequence[Mapping[str, str]], expected: int) -> dict[str, Any]:
    sequences = Counter(attributes.get("event.sequence") for attributes in printed)
    known = {str(index) for index in range(expected)}
    return {
        "delivered_records": len(printed),
        "unique_sequences": len(set(sequences) & known),
        "duplicates": sum(count - 1 for count in sequences.values()),
        "unknown_sequences": len(set(sequences) - known),
        "missing_sequences": len(known - set(sequences)),
        "parsed_records": sum(
            1
            for attributes in printed
            if all(key in attributes for key in COUNT_DIMENSIONS)
        ),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    return accounting(debuglog.log_attributes(sink_log), EVENTS)


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes: list[str] = []
    if sending["http_status"] != 200:
        notes.append(f"the request was answered HTTP {sending['http_status']}")
    for role in ("collector", "sink"):
        accepted = (
            run["counters"].get(role, {}).get("logs", {}).get("receiver_accepted")
        )
        if accepted != sending["records_sent"]:
            notes.append(f"the {role} counted {accepted} records")
    if measured["delivered_records"] != run["counters"].get("sink", {}).get(
        "logs", {}
    ).get("receiver_accepted"):
        notes.append(
            f"the sink printed {measured['delivered_records']} records "
            "and counted another number"
        )
    if measured["duplicates"]:
        notes.append(f"{measured['duplicates']} records were duplicated")
    if measured["unknown_sequences"]:
        notes.append(f"{measured['unknown_sequences']} unknown sequences arrived")
    if measured["missing_sequences"]:
        notes.append(f"{measured['missing_sequences']} sequences are missing")
    if sending["control_count"] != sending["records_sent"]:
        notes.append(f"the control count read {sending['control_count']}")
    for name in ("request_bytes", "batch_item_bytes"):
        if not sending.get(name):
            notes.append(f"never read: {name}")
    return notes


QUESTION = Question(
    id="L3-04",
    title="What do structured logs cost, and what do they save?",
    level=3,
    theme="logs",
    signals=("logs",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("Records sent", "sending.records_sent"),
        Column("Request bytes", "sending.request_bytes"),
        Column(
            "Request bytes per record", "sending.request_bytes_per_record", digits=1
        ),
        Column("Batch item bytes", "sending.batch_item_bytes"),
        Column(
            "Batch bytes per record", "sending.batch_item_bytes_per_record", digits=1
        ),
        Column("Collector accepted", "counters.collector.logs.receiver_accepted"),
        Column("Sink accepted", "counters.sink.logs.receiver_accepted"),
        Column("Delivered", "measured.delivered_records"),
        Column("Unique sequences", "measured.unique_sequences"),
        Column("Parsed records", "measured.parsed_records"),
        Column("Control count", "sending.control_count"),
        Column("Categorized", "sending.categorized_count"),
        Column("Unparsed", "sending.unparsed_count"),
        Column("Request series", "sending.request_series"),
        Column("Miscounted", "sending.miscounted"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
    ),
    interpretation="""
Structured records cost more on the wire and less inside the Collector. The
OTLP/HTTP JSON request for 1000 structured records was 440381 bytes, 440.4 per
record, against 274381 for the canonical text, 60.5% more, because every
attribute travels as a key and a typed value object. After parsing, the order
reversed: the batch processor counted 179536 item bytes for the canonical text,
which kept its full body and gained two attributes, against 163676 for
structured records, 9.7% more. Both differences failed the author's ranges,
the first by half a point above 60% and the second well under 20%.

The drift cost the metric, silently. With 10 of 1000 records in a second format,
the control count still read 1000, the route-by-outcome count put 990 records
in their 10 categories and the other 10 under one `unparsed` series, 11 series
in all, and the Collector logged 0 warn and 0 error lines. Its own counters
accepted and delivered all 1000. Only the `default_value` series shows the loss.

Canonical text and structured records counted identically: 1000 records, 10
series, 0 miscounted, every repetition the same to the byte.

What this does to a bill: structured logs cost about 60% more request bytes in
OTLP/JSON before compression, and save the parser's failure mode. A text format
that drifts keeps every record and loses its dimensions, so an alert on the
`unparsed` series is the only signal that a dashboard has started undercounting.
""",
    does_not_say="""
Measured with OTLP/HTTP JSON, uncompressed, on `otelcol-contrib 0.162.0`, one
request of 1000 records, one regex and one drift pattern. Protobuf encoding and
gzip, which change the byte ratio, are L3-06's subject and were not run here.
Request bytes exclude HTTP headers and TCP; batch bytes are the processor's
item sizes, not a request on the wire.

No log backend was run, so nothing here is stored bytes, index size or a
vendor's bill. The `unparsed` count depends on the parser: a regex anchored
differently or a second pattern would catch other drift.
""",
)
