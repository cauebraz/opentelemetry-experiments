"""Readers for what the `debug` exporter printed.

The sink's log is the only place that says which items arrived together and
which fields survived the pipeline. At `verbosity: basic` the exporter prints
one summary line per export request; at `verbosity: detailed` it also prints
every record. Both shapes are parsed here so a question states what arrived
without counting lines by hand.

Every function takes the raw `docker compose logs` text and strips the
`service-1  | ` prefix itself.
"""

import json
import re
from collections.abc import Iterator

from opentelemetry_experiments.core.stack import unprefixed

SUMMARY_LINE = re.compile(r"\tinfo\t(Traces|Metrics|Logs)\t(?P<fields>\{.*\})$", re.M)

ITEM_KEYS = {
    "Traces": "spans",
    "Metrics": "data points",
    "Logs": "log records",
}

SPAN_BLOCK = re.compile(r"(?m)^Span #\d+$")
LOG_BLOCK = re.compile(r"(?m)^LogRecord #\d+$")

TRACE_ID = re.compile(r"(?m)^\s*Trace ID\s*:[ \t]*(?P<value>[0-9a-f]*)\s*$")
SPAN_ID = re.compile(r"(?m)^\s*ID\s*:[ \t]*(?P<value>[0-9a-f]*)\s*$")
PARENT_ID = re.compile(r"(?m)^\s*Parent ID\s*:[ \t]*(?P<value>[0-9a-f]*)\s*$")

BODY = re.compile(r"(?m)^Body:[ \t]*(?:Str\((?P<string>.*)\)|(?P<other>.*))$")
ATTRIBUTE = re.compile(
    r"(?m)^\s*->\s*(?P<key>[^:]+):[ \t]*(?:\w+\((?P<value>.*)\)|(?P<raw>.*))$"
)


def export_requests(text: str, signal: str | None = None) -> list[int]:
    """Items per export request, one entry per summary line the exporter wrote.

    `signal` of None counts every signal. The value is what the exporter
    reported for that request, which is the batch as it crossed the wire.
    """
    counts = []
    for match in SUMMARY_LINE.finditer(unprefixed(text)):
        name = match.group(1)
        if signal is not None and name.lower() != signal:
            continue
        fields = json.loads(match.group("fields"))
        counts.append(int(fields.get(ITEM_KEYS[name], 0)))
    return counts


def _blocks(text: str, separator: re.Pattern[str]) -> Iterator[str]:
    """Each record printed at `detailed`, from its header to the next one."""
    headers = list(separator.finditer(text))
    if not headers:
        return
    ends = [*(match.start() for match in headers[1:]), len(text)]
    for header, end in zip(headers, ends, strict=True):
        yield text[header.end() : end]


def spans_by_trace(text: str) -> dict[str, set[str]]:
    """Span ids grouped by trace id, so a trace with a hole can be named."""
    traces: dict[str, set[str]] = {}
    for block in _blocks(unprefixed(text), SPAN_BLOCK):
        trace = TRACE_ID.search(block)
        span = SPAN_ID.search(block)
        if trace is None or span is None:
            continue
        traces.setdefault(trace.group("value"), set()).add(span.group("value"))
    return traces


def parents_by_span(text: str) -> dict[str, str]:
    """Parent id per span id. An empty string is a root span."""
    parents: dict[str, str] = {}
    for block in _blocks(unprefixed(text), SPAN_BLOCK):
        span = SPAN_ID.search(block)
        parent = PARENT_ID.search(block)
        if span is None:
            continue
        parents[span.group("value")] = "" if parent is None else parent.group("value")
    return parents


def log_bodies(text: str) -> list[str]:
    """The body of every log record printed, in the order the exporter wrote it."""
    bodies = []
    for block in _blocks(unprefixed(text), LOG_BLOCK):
        match = BODY.search(block)
        if match is None:
            continue
        quoted = match.group("string")
        bodies.append(quoted if quoted is not None else match.group("other"))
    return bodies


def log_attributes(text: str) -> list[dict[str, str]]:
    """One mapping per log record, holding the attributes the exporter printed."""
    records = []
    for block in _blocks(unprefixed(text), LOG_BLOCK):
        records.append(
            {
                match.group("key").strip(): (
                    match.group("value")
                    if match.group("value") is not None
                    else match.group("raw")
                )
                for match in ATTRIBUTE.finditer(block)
            }
        )
    return records


def span_attributes(text: str) -> list[dict[str, str]]:
    """One mapping per span, holding the attributes the exporter printed."""
    records = []
    for block in _blocks(unprefixed(text), SPAN_BLOCK):
        records.append(
            {
                match.group("key").strip(): (
                    match.group("value")
                    if match.group("value") is not None
                    else match.group("raw")
                )
                for match in ATTRIBUTE.finditer(block)
            }
        )
    return records


RESOURCE_SCHEMA_URL = re.compile(r"(?m)^Resource SchemaURL:[ \t]*(?P<value>\S*)\s*$")


def resource_schema_urls(text: str) -> list[str]:
    """Resource SchemaURL lines the exporter printed, in order."""
    return [
        match.group("value") for match in RESOURCE_SCHEMA_URL.finditer(unprefixed(text))
    ]
