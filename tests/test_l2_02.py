import importlib.util

from opentelemetry_experiments.core import stack

PATH = stack.ROOT / "questions" / "L2-02-memory-limiter-placement" / "question.py"
SPEC = importlib.util.spec_from_file_location("l2_02_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_every_record_carries_its_request_and_position():
    assert module.request_ids(3)[:2] == ["r0003-i00", "r0003-i01"]
    assert len(module.request_ids(3)) == module.RECORDS_PER_REQUEST


def test_accounting_separates_acked_loss_from_refused_records():
    statuses = [200, 200, 503]
    delivered = [*module.request_ids(0), *module.request_ids(0)[:3]]

    result = module.accounting(statuses, delivered)

    assert result["delivered_unique"] == 10
    assert result["duplicates"] == 3
    assert result["acked_not_delivered"] == 10
    assert result["refused_records"] == 10
    assert result["refused_delivered"] == 0
    assert result["unknown_delivered"] == 0


def test_accounting_flags_an_id_that_was_never_sent():
    result = module.accounting([200], ["r9999-i00"])

    assert result["unknown_delivered"] == 1


def run(pressure_refused, oom=False, printed=10, at_quiet=10):
    return {
        "sending": {
            "running": True,
            "oom_killed": oom,
            "restarts": 0,
            "sink_at_quiet": at_quiet,
            "limiter_refused": pressure_refused,
        },
        "measured": {
            "records_printed": printed,
            "unknown_delivered": 0,
            "refusing_lines": 0,
        },
    }


def by_name(name):
    return next(variant for variant in module.VARIANTS if variant.name == name)


def test_a_valid_pressure_run_has_no_notes():
    assert module.validate_run(by_name("batch-first"), run(40)) == []


def test_a_pressure_run_where_the_limiter_never_engaged_is_invalid():
    notes = module.validate_run(by_name("limiter-first"), run(0))

    assert notes == ["the limiter never engaged in a pressure variant"]


def test_a_control_where_the_limiter_engaged_is_invalid():
    notes = module.validate_run(by_name("control"), run(10))

    assert notes == ["the limiter engaged in the control"]


def test_an_oom_kill_or_count_mismatch_invalidates_the_run():
    notes = module.validate_run(by_name("control"), run(0, oom=True, at_quiet=9))

    assert len(notes) == 2
