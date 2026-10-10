import importlib.util
from collections import Counter

from opentelemetry_experiments.core import stack

DIRECTORY = stack.ROOT / "questions" / "L3-05-spanmetrics-connector"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


module = load("l3_05_question", "question.py")
emitter = load("l3_05_emitter", "emitter.py")


def test_the_workload_is_balanced_by_route_and_status():
    routes = Counter(emitter.route_of(index) for index in range(emitter.TRACES))
    errors = Counter(
        emitter.route_of(index)
        for index in range(emitter.TRACES)
        if emitter.is_error(index)
    )

    assert set(routes.values()) == {200}
    assert set(errors.values()) == {20}
    assert len({emitter.customer_of(index) for index in range(emitter.TRACES)}) == 1000


def test_ids_repeat_between_generators():
    first, second = emitter.Sequential(), emitter.Sequential()

    assert first.generate_trace_id() == second.generate_trace_id()
    assert first.generate_span_id() == second.generate_span_id()


def test_the_truth_matches_the_workload():
    durations = [
        emitter.ERROR_MS if emitter.is_error(index) else emitter.SUCCESS_MS
        for index in range(emitter.TRACES)
    ]

    assert sum(durations) / len(durations) == module.TRUTH["mean_ms"]
    assert sum(emitter.is_error(index) for index in range(1000)) == 100


def test_workload_summary_flags_imbalance():
    spans = [
        {
            "route": emitter.route_of(index),
            "error": emitter.is_error(index),
            "selected": True,
        }
        for index in range(1000)
    ]

    assert module.workload(spans)["balanced"] is True
    assert module.workload(spans[:-1])["balanced"] is False


def metrics(**overrides):
    base = {
        "series": {"calls": 10, "bucket": 70, "sum": 10, "count": 10},
        "requests": 1000.0,
        "errors": 100.0,
        "duration_sum": 190000.0,
        "duration_count": 1000.0,
    }
    return {**base, **overrides}


def test_accuracy_of_complete_traffic_is_exact():
    result = module.accuracy(metrics(), 1.0, module.TRUTH)

    assert result["combinations"] == 10
    assert result["total_series"] == 100
    assert result["series_per_combination"] == 10.0
    assert result["request_error_pct"] == 0.0
    assert result["error_rate_error_pp"] == 0.0
    assert result["mean_error_pct"] == 0.0


def test_accuracy_scales_a_head_sample():
    sample = metrics(
        requests=98.0, errors=12.0, duration_sum=20600.0, duration_count=98.0
    )

    result = module.accuracy(sample, 10.0, module.TRUTH)

    assert result["requests_scaled"] == 980
    assert result["request_error_pct"] == -2.0
    assert result["error_rate_pct"] == 12.2
    assert result["error_capture_pct"] == 12.0


def run_for(variant, **metric_overrides):
    return {
        "sending": {
            "flushed": True,
            "shutdown": True,
            "exceptions": [],
            "traces_generated": 1000,
            "balanced": True,
            "metrics": metrics(**metric_overrides),
            "decisions": 1000 if variant.options["tail"] else None,
            "dropped_too_early": 0 if variant.options["tail"] else None,
            "running": True,
            "oom_killed": False,
            "restarts": 0,
        },
        "measured": {
            "unknown_traces": 0,
            "spans_printed": 1000,
            "delivered_spans": 1000,
            "delivered_errors": 100,
        },
        "counters": {"sink": {"traces": {"receiver_accepted": 1000}}},
    }


def named(name):
    return next(variant for variant in module.VARIANTS if variant.name == name)


def test_a_clean_run_is_valid():
    variant = named("unbounded-tail-errors")

    assert module.validate_run(variant, run_for(variant)) == []


def test_validity_names_every_failure():
    variant = named("unbounded-tail-errors")
    run = run_for(
        variant,
        requests=999.0,
        duration_count=998.0,
        errors=99.0,
        series={"calls": 10, "bucket": 0, "sum": 10, "count": 10},
    )
    run["sending"].update({"decisions": 999, "dropped_too_early": 1, "restarts": 1})
    run["measured"]["unknown_traces"] = 1

    notes = " | ".join(module.validate_run(variant, run))

    for fragment in (
        "unknown traces",
        "calls read 999",
        "histogram counted 998",
        "error calls read 99",
        "families missing: bucket",
        "decided 999",
        "dropped 1",
        "did not survive",
    ):
        assert fragment in notes


def test_configs_put_spanmetrics_after_sampling_and_bound_dimensions():
    for variant in module.VARIANTS:
        config = (stack.ROOT / variant.collector_config).read_text()
        assert ("tail_sampling" in config) == variant.options["tail"]
        assert ("customer.id" in config) == variant.name.startswith("unbounded")
        assert variant.collector_memory_limit == "512m"
        assert variant.sink_config == stack.SINK_DETAILED_CONFIG
        if variant.options["tail"]:
            processors = config.split("processors:")[-1]
            assert processors.index("tail_sampling") < processors.index("batch")
