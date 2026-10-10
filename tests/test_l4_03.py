import importlib.util

from opentelemetry_experiments.core import debuglog, otlp, stack

PATH = stack.ROOT / "questions" / "L4-03-semconv-migration" / "question.py"
SPEC = importlib.util.spec_from_file_location("l4_03_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_historical_and_live_share_deterministic_ids():
    historical = module.historical_spans()
    live = module.live_spans()

    assert len(historical) == 100
    assert len(live) == 100
    assert [span["traceId"] for span in historical] == [
        span["traceId"] for span in live
    ]
    assert [span["spanId"] for span in historical] == [span["spanId"] for span in live]


def test_ten_live_spans_already_carry_both_names():
    live = module.live_spans()
    both = [
        span
        for span in live
        if {item["key"] for item in span["attributes"]}
        >= {module.OLD_KEY, module.NEW_KEY}
    ]

    assert len(both) == 10


def test_trace_payload_carries_the_schema_url():
    payload = otlp.trace_payload(
        [otlp.span("x")], {"service.name": "lab"}, schema_url=module.OLD_SCHEMA
    )

    assert payload["resourceSpans"][0]["schemaUrl"] == module.OLD_SCHEMA


def test_span_attributes_and_resource_schema_urls_parse():
    text = """\
sink-1  | ResourceSpans #0
sink-1  | Resource SchemaURL: http://schemas/1.21.0
sink-1  | ScopeSpans #0
sink-1  | Span #0
sink-1  | Attributes:
sink-1  |      -> http.status_code: Int(200)
sink-1  |      -> phase: Str(historical)
sink-1  | Span #1
sink-1  | Attributes:
sink-1  |      -> http.response.status_code: Int(200)
sink-1  |      -> phase: Str(live)
"""

    assert debuglog.span_attributes(text) == [
        {"http.status_code": "200", "phase": "historical"},
        {"http.response.status_code": "200", "phase": "live"},
    ]
    assert debuglog.resource_schema_urls(text) == ["http://schemas/1.21.0"]


def test_query_coverage_counts_each_shape_once():
    coverage = module.query_coverage(
        [
            {"http.status_code": "200"},
            {"http.response.status_code": "200"},
            {
                "http.status_code": "200",
                "http.response.status_code": "200",
            },
        ]
    )

    assert coverage == {
        "old_only": 1,
        "new_only": 1,
        "either": 3,
        "both": 1,
    }


def test_validate_run_accepts_a_matching_hard_rename():
    run = {
        "sending": {
            "historical_delivered": 100,
            "total_delivered": 200,
            "coverage_either": 200,
            "live_new_only": 100,
            "both_kept": 0,
            "collector_running": True,
            "collector_oom": False,
            "resource_schema_urls": [module.NEW_SCHEMA],
        },
        "measured": {"printed_spans": 200, "unique_traces": 100},
    }

    assert module.validate_run(module.VARIANTS[1], run) == []
