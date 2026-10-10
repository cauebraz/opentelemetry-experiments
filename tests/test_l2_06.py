import importlib.util

from opentelemetry_experiments.core import stack

PATH = stack.ROOT / "questions" / "L2-06-sdk-without-collector" / "question.py"
SPEC = importlib.util.spec_from_file_location("l2_06_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def spans(phases):
    return [
        {"index": index, "phase": phase, "trace_id": f"{index:032x}", "end_ms": 0.1}
        for index, phase in enumerate(phases)
    ]


def test_ranges_collapse_consecutive_indices():
    assert module.ranges([0, 1, 2, 5, 7, 8]) == "0-2,5,7-8"
    assert module.ranges([]) == "none"


def test_accounting_splits_loss_by_phase_and_flags_foreign_traces():
    emitted = spans(["down", "down", "up", "up"])
    arrived = {f"{1:032x}": {"a"}, f"{3:032x}": {"b"}, "f" * 32: {"c"}}

    result = module.accounting(emitted, arrived)

    assert result["delivered"] == 2
    assert result["lost_down"] == 1
    assert result["lost_up"] == 1
    assert result["delivered_ranges"] == "1,3"
    assert result["unknown_traces"] == 1


def test_emitter_summary_counts_the_sdk_log():
    summary = module.summarize_emitter(
        {
            "spans": spans(["down", "up"]),
            "exceptions": [],
            "collector_ready_seconds": 1.2,
            "flushed": True,
            "seconds": 3.0,
            "sdk_log": [
                {
                    "level": "warning",
                    "logger": "x",
                    "message": "Queue full, dropping span.",
                },
                {
                    "level": "error",
                    "logger": "y",
                    "message": "Failed to export spans batch due to timeout",
                },
            ],
        }
    )

    assert summary["spans_down"] == 1
    assert summary["sdk_warnings"] == 1
    assert summary["sdk_errors"] == 1
    assert summary["queue_full_logs"] == 1
    assert summary["export_failed_logs"] == 1


def test_a_sink_count_that_disagrees_with_its_log_is_invalid():
    run = {
        "sending": {"spans_down": 0, "collector_ready_seconds": None},
        "counters": {"sink": {"traces": {"receiver_accepted": 5.0}}},
        "measured": {"spans_printed": 4, "unknown_traces": 0},
    }

    notes = module.validate_run(module.VARIANTS[0], run)

    assert notes == ["the sink counted 5.0 spans but printed 4"]
