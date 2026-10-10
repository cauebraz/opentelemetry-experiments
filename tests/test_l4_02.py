import importlib.util
from collections import Counter

from opentelemetry_experiments.core import stack

PATH = stack.ROOT / "questions" / "L4-02-view-vs-transform" / "question.py"
EMITTER = stack.ROOT / "questions" / "L4-02-view-vs-transform" / "emitter.py"
SPEC = importlib.util.spec_from_file_location("l4_02_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
EMITTER_SPEC = importlib.util.spec_from_file_location("l4_02_emitter", EMITTER)
emitter = importlib.util.module_from_spec(EMITTER_SPEC)
EMITTER_SPEC.loader.exec_module(emitter)


def test_workload_is_six_hundred_observations_over_two_hundred_users():
    rows = emitter.observations()
    users = {row["user.id"] for row in rows}
    methods = {row["http.method"] for row in rows}
    statuses = {row["http.status_code"] for row in rows}

    assert len(rows) == 600
    assert len(users) == 200
    assert methods == set(emitter.METHODS)
    assert statuses == set(emitter.STATUSES)
    assert len({(row["http.method"], row["http.status_code"]) for row in rows}) == 20


def test_each_user_appears_three_times_with_the_same_value_pattern():
    rows = emitter.observations()
    by_user = Counter(row["user.id"] for row in rows)
    values = [row["value"] for row in rows]

    assert set(by_user.values()) == {3}
    assert values[:7] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert max(values) == 7.0
    assert min(values) == 1.0


def test_kept_keys_exclude_user_id():
    assert "user.id" not in emitter.KEPT
    assert {
        "environment",
        "region",
        "http.method",
        "http.status_code",
    } == emitter.KEPT


def test_variants_cover_baseline_view_and_transform():
    names = [variant.name for variant in module.VARIANTS]
    assert names == ["baseline", "sdk-view", "collector-transform"]
    by_name = {variant.name: variant for variant in module.VARIANTS}
    assert by_name["baseline"].options["use_view"] is False
    assert by_name["sdk-view"].options["use_view"] is True
    assert by_name["collector-transform"].options["use_view"] is False
    assert "transform" in by_name["collector-transform"].collector_config


def test_validate_run_accepts_a_matching_baseline():
    variant = module.VARIANTS[0]
    run = {
        "sending": {
            "observations_sent": 600,
            "flushed": True,
            "prometheus_restored": True,
            "metric_observations": 600,
            "metric_identity_series": 200,
            "metric_family_series": 2200,
            "metric_user_id_after": True,
            "metric_inbound_user_values": 200,
            "metric_inbound_with_user_id": 200,
            "collector_running": True,
            "collector_oom": False,
            "collector_restarts": 0,
            "window_problems": [],
        },
        "measured": {"delivered_datapoints": 200},
        "counters": {
            "collector": {"metrics": {"receiver_refused": 0, "exporter_send_failed": 0}}
        },
    }

    assert module.validate_run(variant, run) == []


def test_validate_run_rejects_sdk_view_that_leaked_user_id():
    variant = module.VARIANTS[1]
    run = {
        "sending": {
            "observations_sent": 600,
            "flushed": True,
            "prometheus_restored": True,
            "metric_observations": 600,
            "metric_identity_series": 20,
            "metric_family_series": 220,
            "metric_user_id_after": False,
            "metric_inbound_user_values": 12,
            "metric_inbound_with_user_id": 12,
            "collector_running": True,
            "collector_oom": False,
            "collector_restarts": 0,
            "window_problems": [],
        },
        "measured": {"delivered_datapoints": 20},
        "counters": {
            "collector": {"metrics": {"receiver_refused": 0, "exporter_send_failed": 0}}
        },
    }

    notes = module.validate_run(variant, run)
    assert any("presented user.id" in note for note in notes)
