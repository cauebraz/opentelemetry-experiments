import pytest

from opentelemetry_experiments.core import prometheus


def test_resolve_name_accepts_the_counter_with_or_without_the_total_suffix():
    available = {"otelcol_receiver_accepted_spans_total", "otelcol_exporter_sent_spans"}

    assert (
        prometheus.resolve_name(available, "otelcol_receiver_accepted_spans")
        == "otelcol_receiver_accepted_spans_total"
    )
    assert (
        prometheus.resolve_name(available, "otelcol_exporter_sent_spans")
        == "otelcol_exporter_sent_spans"
    )
    assert prometheus.resolve_name(available, "otelcol_receiver_refused_spans") is None


def test_flatten_drops_none_and_keeps_the_path():
    flat = prometheus.flatten(
        {"collector": {"traces": {"receiver_accepted": 1, "receiver_refused": None}}}
    )

    assert flat == {"collector.traces.receiver_accepted": 1.0}


def test_wait_until_stable_returns_once_the_sample_repeats():
    samples = iter([{"a": 1}, {"a": 2}, {"a": 2}, {"a": 2}, {"a": 2}])

    observed = prometheus.wait_until_stable(
        lambda: next(samples),
        stable_rounds=2,
        poll_seconds=0,
    )

    assert observed == {"a": 2}


def test_wait_until_stable_refuses_an_all_zero_sample_when_nonzero_is_required():
    with pytest.raises(TimeoutError):
        prometheus.wait_until_stable(
            lambda: {"a": 0},
            stable_rounds=2,
            poll_seconds=0,
            timeout_seconds=0.2,
        )


def test_wait_until_stable_accepts_an_all_zero_sample_when_zero_is_the_answer():
    observed = prometheus.wait_until_stable(
        lambda: {"a": 0},
        stable_rounds=2,
        poll_seconds=0,
        require_nonzero=False,
    )

    assert observed == {"a": 0}


def test_series_names_asks_for_one_job_and_keeps_the_names(monkeypatch):
    seen = []

    def fake_read_json(url):
        seen.append(url)
        return {
            "data": [
                {"__name__": "otelcol_receiver_accepted_spans", "job": "collector"},
                {"__name__": "otelcol_receiver_accepted_spans", "job": "collector"},
                {"__name__": "otelcol_exporter_sent_spans", "job": "collector"},
                {"job": "collector"},
            ]
        }

    monkeypatch.setattr(prometheus, "read_json", fake_read_json)

    names = prometheus.series_names("http://prom", "collector")

    assert names == {
        "otelcol_receiver_accepted_spans",
        "otelcol_exporter_sent_spans",
    }
    assert seen == ["http://prom/api/v1/series?match%5B%5D=%7Bjob%3D%22collector%22%7D"]


def test_counters_keep_an_absent_job_sample_as_missing(monkeypatch):
    monkeypatch.setattr(
        prometheus,
        "series_names",
        lambda base_url, job: {"otelcol_receiver_accepted_spans"},
    )
    monkeypatch.setattr(prometheus, "query_result", lambda base_url, query: [])

    observed = prometheus.counters("http://prom", "sink", ("traces",))

    assert observed["traces"]["receiver_accepted"] is None


def test_counters_discover_names_for_the_requested_job(monkeypatch):
    seen = []

    def fake_series_names(base_url, job):
        seen.append((base_url, job))
        return set()

    monkeypatch.setattr(prometheus, "series_names", fake_series_names)
    monkeypatch.setattr(prometheus, "query_value", lambda base_url, query: 1)

    prometheus.counters("http://prom", "sink", ("traces",))

    assert seen == [("http://prom", "sink")]


def test_counters_hide_stale_samples_while_the_target_is_down(monkeypatch):
    def fake_query_value(base_url, query):
        if query == 'up{job="sink"}':
            return 0
        raise AssertionError(f"a down target must not be queried: {query}")

    monkeypatch.setattr(prometheus, "query_value", fake_query_value)

    observed = prometheus.counters("http://prom", "sink", ("traces",))

    assert observed["traces"] == {
        "receiver_accepted": None,
        "receiver_refused": None,
        "exporter_sent": None,
        "exporter_send_failed": None,
    }


def test_every_signal_has_a_unit_used_by_the_counter_names():
    assert set(prometheus.SIGNAL_UNITS) == {"traces", "metrics", "logs"}
    for unit in prometheus.SIGNAL_UNITS.values():
        for template in prometheus.COUNTER_ROLES.values():
            assert template.format(unit=unit).startswith("otelcol_")
