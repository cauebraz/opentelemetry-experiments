"""Hand-built OTLP payloads.

Level 0 sends OTLP/HTTP JSON so every field is visible in the file that
produced it. The protobuf path is used from level 1 on, where volume matters
more than readability.
"""

import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

JSON_HEADERS = {"Content-Type": "application/json"}

DELTA = "AGGREGATION_TEMPORALITY_DELTA"
CUMULATIVE = "AGGREGATION_TEMPORALITY_CUMULATIVE"

SCOPE_NAME = "opentelemetry_experiments"


def now_unix_nano() -> int:
    return time.time_ns()


def hex_id(byte_length: int) -> str:
    return secrets.token_hex(byte_length)


def any_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def attributes(pairs: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": any_value(value)} for key, value in sorted(pairs.items())
    ]


def scope(name: str = SCOPE_NAME) -> dict[str, Any]:
    return {"name": name}


ENVELOPE_KEYS = {
    "traces": ("resourceSpans", "scopeSpans", "spans"),
    "metrics": ("resourceMetrics", "scopeMetrics", "metrics"),
    "logs": ("resourceLogs", "scopeLogs", "logRecords"),
}


def _envelope(
    signal: str,
    resource_attributes: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
    schema_url: str | None = None,
) -> dict[str, Any]:
    """One resource, one scope. `resource_attributes` of None omits the resource."""
    resource_key, scope_key, record_key = ENVELOPE_KEYS[signal]
    inner: dict[str, Any] = {"scope": scope(), record_key: list(records)}
    outer: dict[str, Any] = {scope_key: [inner]}
    if resource_attributes is not None:
        outer["resource"] = {"attributes": attributes(resource_attributes)}
    if schema_url is not None:
        outer["schemaUrl"] = schema_url
    return {resource_key: [outer]}


def span(
    name: str,
    trace_id: str | None = None,
    span_id: str | None = None,
    start_unix_nano: int | None = None,
    end_unix_nano: int | None = None,
    kind: int = 2,
    span_attributes: Mapping[str, Any] | None = None,
    parent_span_id: str | None = None,
) -> dict[str, Any]:
    start = now_unix_nano() if start_unix_nano is None else start_unix_nano
    record: dict[str, Any] = {
        "traceId": hex_id(16) if trace_id is None else trace_id,
        "spanId": hex_id(8) if span_id is None else span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start),
        "attributes": attributes(span_attributes or {}),
    }
    if parent_span_id is not None:
        record["parentSpanId"] = parent_span_id
    if end_unix_nano is not None:
        record["endTimeUnixNano"] = str(end_unix_nano)
    return record


def trace_payload(
    spans: Sequence[Mapping[str, Any]],
    resource_attributes: Mapping[str, Any] | None = None,
    schema_url: str | None = None,
) -> dict[str, Any]:
    return _envelope("traces", resource_attributes, spans, schema_url=schema_url)


def number_point(
    value: float,
    point_attributes: Mapping[str, Any] | None = None,
    start_unix_nano: int | None = None,
    time_unix_nano: int | None = None,
) -> dict[str, Any]:
    moment = now_unix_nano() if time_unix_nano is None else time_unix_nano
    start = moment if start_unix_nano is None else start_unix_nano
    return {
        "asDouble": value,
        "startTimeUnixNano": str(start),
        "timeUnixNano": str(moment),
        "attributes": attributes(point_attributes or {}),
    }


def sum_metric(
    name: str,
    points: Sequence[Mapping[str, Any]],
    temporality: str | None = DELTA,
    monotonic: bool = True,
    unit: str = "1",
) -> dict[str, Any]:
    """`temporality` of None leaves the field out, which is UNSPECIFIED."""
    body: dict[str, Any] = {"dataPoints": list(points), "isMonotonic": monotonic}
    if temporality is not None:
        body["aggregationTemporality"] = temporality
    return {"name": name, "unit": unit, "sum": body}


def gauge_metric(
    name: str,
    points: Sequence[Mapping[str, Any]],
    unit: str = "1",
) -> dict[str, Any]:
    return {"name": name, "unit": unit, "gauge": {"dataPoints": list(points)}}


def exponential_histogram_point(
    count: int,
    sum_value: float,
    scale: int,
    zero_count: int,
    positive_offset: int,
    positive_buckets: Sequence[int],
    point_attributes: Mapping[str, Any] | None = None,
    start_unix_nano: int | None = None,
    time_unix_nano: int | None = None,
) -> dict[str, Any]:
    """One Delta/Cumulative exponential-histogram datapoint."""
    moment = now_unix_nano() if time_unix_nano is None else time_unix_nano
    start = moment if start_unix_nano is None else start_unix_nano
    return {
        "attributes": attributes(point_attributes or {}),
        "startTimeUnixNano": str(start),
        "timeUnixNano": str(moment),
        "count": str(count),
        "sum": sum_value,
        "scale": scale,
        "zeroCount": str(zero_count),
        "positive": {
            "offset": positive_offset,
            "bucketCounts": [str(value) for value in positive_buckets],
        },
    }


def exponential_histogram_metric(
    name: str,
    points: Sequence[Mapping[str, Any]],
    temporality: str | None = DELTA,
    unit: str = "ms",
) -> dict[str, Any]:
    body: dict[str, Any] = {"dataPoints": list(points)}
    if temporality is not None:
        body["aggregationTemporality"] = temporality
    return {"name": name, "unit": unit, "exponentialHistogram": body}


def metric_payload(
    metrics: Sequence[Mapping[str, Any]],
    resource_attributes: Mapping[str, Any] | None = None,
    schema_url: str | None = None,
) -> dict[str, Any]:
    return _envelope("metrics", resource_attributes, metrics, schema_url=schema_url)


def log_record(
    body: str,
    severity_number: int = 9,
    severity_text: str = "INFO",
    time_unix_nano: int | None = None,
    observed_time_unix_nano: int | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    record_attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`time_unix_nano` of None leaves the field out, as a log with no timestamp."""
    record: dict[str, Any] = {
        "body": any_value(body),
        "severityNumber": severity_number,
        "severityText": severity_text,
        "attributes": attributes(record_attributes or {}),
    }
    if time_unix_nano is not None:
        record["timeUnixNano"] = str(time_unix_nano)
    if observed_time_unix_nano is not None:
        record["observedTimeUnixNano"] = str(observed_time_unix_nano)
    if trace_id is not None:
        record["traceId"] = trace_id
    if span_id is not None:
        record["spanId"] = span_id
    return record


def log_payload(
    records: Sequence[Mapping[str, Any]],
    resource_attributes: Mapping[str, Any] | None = None,
    schema_url: str | None = None,
) -> dict[str, Any]:
    return _envelope("logs", resource_attributes, records, schema_url=schema_url)


SIGNAL_PATHS = {
    "traces": "/v1/traces",
    "metrics": "/v1/metrics",
    "logs": "/v1/logs",
}


def post_json(
    base_url: str,
    signal: str,
    payload: Mapping[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST an OTLP/HTTP JSON payload and record exactly what came back."""
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{SIGNAL_PATHS[signal]}",
        data=encoded,
        headers=JSON_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "http_status": response.status,
                "response_body": response.read().decode("utf-8"),
                "request_bytes": len(encoded),
            }
    except urllib.error.HTTPError as error:
        return {
            "http_status": error.code,
            "response_body": error.read().decode("utf-8"),
            "request_bytes": len(encoded),
        }


def curl_command(base_url: str, signal: str, payload: Mapping[str, Any]) -> str:
    """The equivalent one-liner, so the question can be run without Python."""
    body = json.dumps(payload, separators=(",", ":"))
    return (
        f"curl -i -X POST {base_url}{SIGNAL_PATHS[signal]} "
        f"-H 'Content-Type: application/json' -d '{body}'"
    )
