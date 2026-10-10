from opentelemetry_experiments.core import debuglog, stack

SUMMARY = (
    'sink-1  | \tinfo\tMetrics\t{"otelcol.component.id": "debug",'
    ' "otelcol.signal": "metrics", "resource metrics": 1, "metrics": 1,'
    ' "data points": 40}\n'
    'sink-1  | \tinfo\tMetrics\t{"otelcol.component.id": "debug",'
    ' "otelcol.signal": "metrics", "resource metrics": 1, "metrics": 1,'
    ' "data points": 60}\n'
    'sink-1  | \tinfo\tTraces\t{"otelcol.component.id": "debug",'
    ' "otelcol.signal": "traces", "resource spans": 1, "spans": 2}\n'
)

SPANS = """\
sink-1  | ResourceSpans #0
sink-1  | Resource attributes:
sink-1  |      -> service.name: Str(l2-05)
sink-1  | ScopeSpans #0
sink-1  | Span #0
sink-1  |     Trace ID       : aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
sink-1  |     Parent ID      :
sink-1  |     ID             : 1111111111111111
sink-1  |     Name           : root
sink-1  | Span #1
sink-1  |     Trace ID       : aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
sink-1  |     Parent ID      : 1111111111111111
sink-1  |     ID             : 2222222222222222
sink-1  |     Name           : child
sink-1  | Span #2
sink-1  |     Trace ID       : bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
sink-1  |     Parent ID      :
sink-1  |     ID             : 3333333333333333
sink-1  |     Name           : root
"""

RECORDS = """\
sink-1  | ResourceLogs #0
sink-1  | LogRecord #0
sink-1  | SeverityText: INFO
sink-1  | Body: Str(14:02:01 INFO checkout started)
sink-1  | Attributes:
sink-1  |      -> level: Str(INFO)
sink-1  |      -> msg: Str(checkout started)
sink-1  | LogRecord #1
sink-1  | SeverityText:
sink-1  | Body: Str({"partial": true)
sink-1  | Attributes:
sink-1  |      -> log.file.name: Str(app.log)
"""


def test_export_requests_counts_the_items_of_each_request():
    assert debuglog.export_requests(SUMMARY, "metrics") == [40, 60]
    assert debuglog.export_requests(SUMMARY, "traces") == [2]
    assert debuglog.export_requests(SUMMARY) == [40, 60, 2]


def test_a_log_without_an_export_has_no_requests():
    assert debuglog.export_requests("sink-1  | \tinfo\tStarting\n") == []


def test_spans_are_grouped_by_their_trace():
    assert debuglog.spans_by_trace(SPANS) == {
        "a" * 32: {"1" * 16, "2" * 16},
        "b" * 32: {"3" * 16},
    }


def test_the_parent_field_tells_a_root_from_a_child():
    assert debuglog.parents_by_span(SPANS) == {
        "1" * 16: "",
        "2" * 16: "1" * 16,
        "3" * 16: "",
    }


def test_log_bodies_keep_the_line_that_the_parser_left_alone():
    assert debuglog.log_bodies(RECORDS) == [
        "14:02:01 INFO checkout started",
        '{"partial": true',
    ]


def test_log_attributes_are_read_per_record():
    assert debuglog.log_attributes(RECORDS) == [
        {"level": "INFO", "msg": "checkout started"},
        {"log.file.name": "app.log"},
    ]


def test_a_recorded_sink_log_parses_the_span_it_printed():
    recorded = (
        stack.QUESTIONS
        / "L1-03-env-vars-and-two-pipelines"
        / "results"
        / "logs"
        / "one-mutates-r1-sink.log"
    ).read_text(encoding="utf-8")

    traces = debuglog.spans_by_trace(recorded)

    assert len(traces) == 1
    assert [len(spans) for spans in traces.values()] == [1]
    assert debuglog.export_requests(recorded, "traces") == [1]
