import json

import pytest

from opentelemetry_experiments.core import otlp


def test_each_signal_uses_its_own_envelope_keys():
    traces = otlp.trace_payload([otlp.span("GET /")], {"service.name": "lab"})
    metrics = otlp.metric_payload([otlp.gauge_metric("m", [otlp.number_point(1)])])
    logs = otlp.log_payload([otlp.log_record("hello", time_unix_nano=1)])

    assert list(traces) == ["resourceSpans"]
    assert list(traces["resourceSpans"][0]["scopeSpans"][0]) == ["scope", "spans"]
    assert list(metrics) == ["resourceMetrics"]
    assert list(metrics["resourceMetrics"][0]["scopeMetrics"][0]) == [
        "scope",
        "metrics",
    ]
    assert list(logs) == ["resourceLogs"]
    assert list(logs["resourceLogs"][0]["scopeLogs"][0]) == ["scope", "logRecords"]


def test_a_resource_of_none_leaves_the_resource_out():
    with_resource = otlp.trace_payload([otlp.span("a")], {"service.name": "lab"})
    without = otlp.trace_payload([otlp.span("a")], None)

    assert "resource" in with_resource["resourceSpans"][0]
    assert "resource" not in without["resourceSpans"][0]


def test_a_span_without_an_end_time_omits_the_field():
    assert "endTimeUnixNano" not in otlp.span("a")
    assert otlp.span("a", end_unix_nano=7)["endTimeUnixNano"] == "7"


def test_a_span_without_a_parent_omits_the_field():
    assert "parentSpanId" not in otlp.span("a")
    child = otlp.span("a", parent_span_id="aaaaaaaaaaaaaaaa")
    assert child["parentSpanId"] == "aaaaaaaaaaaaaaaa"


def test_a_short_trace_id_is_sent_as_written():
    short = otlp.span("a", trace_id="0" * 30)
    assert short["traceId"] == "0" * 30
    assert len(otlp.span("a")["traceId"]) == 32
    assert len(otlp.span("a")["spanId"]) == 16


def test_a_sum_without_temporality_omits_the_field():
    without = otlp.sum_metric("m", [otlp.number_point(1)], temporality=None)
    delta = otlp.sum_metric("m", [otlp.number_point(1)])

    assert "aggregationTemporality" not in without["sum"]
    assert delta["sum"]["aggregationTemporality"] == otlp.DELTA


def test_a_log_record_omits_the_timestamp_and_the_trace_id_when_not_given():
    bare = otlp.log_record("hello")
    linked = otlp.log_record("hello", time_unix_nano=5, trace_id="a" * 32)

    assert "timeUnixNano" not in bare
    assert "traceId" not in bare
    assert linked["timeUnixNano"] == "5"
    assert linked["traceId"] == "a" * 32


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, {"boolValue": True}),
        (3, {"intValue": "3"}),
        (1.5, {"doubleValue": 1.5}),
        ("lab", {"stringValue": "lab"}),
    ],
)
def test_any_value_picks_the_field_from_the_python_type(value, expected):
    assert otlp.any_value(value) == expected


def test_attributes_are_sorted_so_payloads_are_comparable():
    pairs = otlp.attributes({"b": 1, "a": 2})
    assert [pair["key"] for pair in pairs] == ["a", "b"]


def test_the_curl_command_carries_the_same_json_as_post_json():
    payload = otlp.trace_payload([otlp.span("a", trace_id="b" * 32)])
    command = otlp.curl_command("http://127.0.0.1:24318", "traces", payload)

    assert command.startswith("curl -i -X POST http://127.0.0.1:24318/v1/traces ")
    body = command.split(" -d '", 1)[1].rsplit("'", 1)[0]
    assert json.loads(body) == payload
