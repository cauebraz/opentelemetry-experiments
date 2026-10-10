from opentelemetry_experiments.core import report


def test_get_walks_a_dotted_path_and_returns_none_for_a_missing_step():
    payload = {"counters": {"collector": {"traces": {"receiver_accepted": 4}}}}

    assert report.get(payload, "counters.collector.traces.receiver_accepted") == 4
    assert report.get(payload, "counters.sink.traces.receiver_accepted") is None
    assert report.get(
        payload, "counters.collector.traces.receiver_accepted.deeper"
    ) is (None)


def test_stats_ignores_missing_repetitions():
    assert report.stats([2, None, 4, 6]) == {
        "median": 4.0,
        "minimum": 2.0,
        "maximum": 6.0,
        "samples": 3,
    }
    assert report.stats([None, None]) is None


def test_agreement_reports_whether_the_repetitions_said_the_same_thing():
    same = report.agreement(["400", "400", "400"])
    differ = report.agreement(["400", "200"])

    assert same == {"value": "400", "distinct": ["400"], "agrees": True, "samples": 3}
    assert differ["agrees"] is False
    assert differ["value"] is None
    assert differ["distinct"] == ["400", "200"]


def test_a_number_cell_shows_the_range_only_when_the_repetitions_disagreed():
    assert report.number_cell(report.stats([3, 3, 3])) == "3"
    assert report.number_cell(report.stats([3, 4, 5])) == "4 (3 to 5)"
    assert report.number_cell(report.stats([1.25, 1.25]), digits=2) == "1.25"
    assert report.number_cell(None) == report.MISSING


def test_a_text_cell_marks_disagreement_and_escapes_the_pipe():
    assert report.text_cell(report.agreement(["ok", "ok"])) == "ok"
    assert report.text_cell(report.agreement(["ok", "no"])) == "varies: ok, no"
    assert report.text_cell(report.agreement(["a|b"])) == "a\\|b"
    assert report.text_cell(None) == report.MISSING


def test_truncate_collapses_whitespace_and_marks_the_cut():
    assert report.truncate("a   b\nc", 80) == "a b c"
    assert report.truncate("abcdefghij", 8) == "abcde..."


def test_table_renders_a_header_separator_and_one_line_per_row():
    lines = report.table(("A", "B"), [("1", "2"), ("3", "4")])

    assert lines == [
        "| A | B |",
        "| --- | --- |",
        "| 1 | 2 |",
        "| 3 | 4 |",
    ]
