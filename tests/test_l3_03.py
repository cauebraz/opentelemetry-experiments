import importlib.util

from opentelemetry_experiments.core import stack

DIRECTORY = stack.ROOT / "questions" / "L3-03-head-vs-tail-sampling"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


module = load("l3_03_question", "question.py")
emitter = load("l3_03_emitter", "emitter.py")


def sending(**overrides):
    generated = [f"{index:032x}" for index in range(10)]
    base = {
        "generated": generated,
        "errors": generated[:2],
        "selected": generated,
        "spans_per_trace": 5,
        "flushed": True,
        "shutdown": True,
        "exceptions": [],
    }
    return {**base, **overrides}


def spans(count):
    return {f"s{index}" for index in range(count)}


def test_the_workload_has_exactly_100_error_traces_and_stable_ids():
    errors = [index for index in range(emitter.TRACES) if emitter.is_error(index)]
    ids = {emitter.trace_id_for(index) for index in range(emitter.TRACES)}

    assert len(errors) == 100
    assert len(ids) == emitter.TRACES
    assert emitter.trace_id_for(7) == emitter.trace_id_for(7)


def test_the_id_generator_repeats_the_same_sequence():
    first, second = emitter.Sequential(), emitter.Sequential()

    assert [first.generate_trace_id() for _ in range(3)] == [
        second.generate_trace_id() for _ in range(3)
    ]
    assert first.generate_span_id() == second.generate_span_id()


def test_accounting_splits_errors_successes_and_incomplete_traces():
    sent = sending()
    arrived = {
        sent["generated"][0]: spans(5),
        sent["generated"][1]: spans(3),
        sent["generated"][5]: spans(5),
        "f" * 32: spans(5),
    }

    result = module.accounting(sent, arrived)

    assert result["delivered_traces"] == 3
    assert result["delivered_spans"] == 13
    assert result["complete_traces"] == 2
    assert result["incomplete_traces"] == 1
    assert result["error_traces_delivered"] == 2
    assert result["success_traces_delivered"] == 1
    assert result["unknown_traces"] == 1
    assert result["error_capture_pct"] == 100.0
    assert result["retained_pct"] == 26.0
    assert result["request_ratio"] == 0.3
    assert result["observed_error_rate_pct"] == 66.7


def test_accounting_without_deliveries_has_no_error_rate():
    result = module.accounting(sending(), {})

    assert result["delivered_traces"] == 0
    assert result["observed_error_rate_pct"] is None


def valid_run(variant, **sending_overrides):
    sent = sending(**sending_overrides)
    tail = variant.options["tail"]
    sent.update(
        {
            "decisions": 10 if tail else None,
            "dropped_too_early": 0 if tail else None,
            "running": True,
            "oom_killed": False,
            "restarts": 0,
            "heap_peak_mib": 30.0,
            "rss_peak_mib": 200.0,
            "drain_seconds": 6.0,
        }
    )
    arrived = {trace: spans(5) for trace in sent["generated"]}
    measured = {**module.accounting(sent, arrived), "spans_printed": 50}
    return {
        "sending": sent,
        "measured": measured,
        "counters": {"sink": {"traces": {"receiver_accepted": 50}}},
    }


def variant_named(name):
    return next(variant for variant in module.VARIANTS if variant.name == name)


def test_a_clean_tail_run_is_valid():
    variant = variant_named("tail-errors")

    assert module.validate_run(variant, valid_run(variant)) == []


def test_validity_names_every_failure():
    variant = variant_named("tail-errors-plus-10")
    run = valid_run(variant, flushed=False, exceptions=["ValueError: x"], generated=[])
    run["sending"].update(
        {"decisions": 9, "dropped_too_early": 1, "restarts": 1, "oom_killed": True}
    )
    run["measured"].update({"unknown_traces": 2, "incomplete_traces": 1})
    run["counters"]["sink"]["traces"]["receiver_accepted"] = 49

    notes = " | ".join(module.validate_run(variant, run))

    for fragment in (
        "did not flush",
        "raised",
        "never generated",
        "incomplete",
        "counted 49",
        "decided 9",
        "dropped 1",
        "did not survive",
    ):
        assert fragment in notes


def test_a_peak_or_drain_never_read_invalidates_the_run():
    variant = variant_named("all")
    run = valid_run(variant)
    run["sending"]["heap_peak_mib"] = None
    run["sending"]["drain_seconds"] = None

    assert module.validate_run(variant, run) == [
        "never read: heap_peak_mib, drain_seconds"
    ]


def test_decisions_are_not_required_without_tail_sampling():
    variant = variant_named("head-10")

    assert module.validate_run(variant, valid_run(variant)) == []


def test_every_tail_variant_uses_tail_sampling_and_the_ceiling():
    for variant in module.VARIANTS:
        config = (stack.ROOT / variant.collector_config).read_text()
        assert ("tail_sampling" in config) == variant.options["tail"]
        assert variant.collector_memory_limit == "512m"
        assert variant.sink_config == stack.SINK_DETAILED_CONFIG
