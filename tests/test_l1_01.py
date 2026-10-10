import importlib.util

from opentelemetry_experiments.core import stack

DIRECTORY = stack.ROOT / "questions" / "L1-01-pipeline-from-zero"
spec = importlib.util.spec_from_file_location(
    "l1_01_question", DIRECTORY / "question.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_every_case_changes_the_minimal_config_and_exists():
    minimal = (DIRECTORY / "config" / "minimal.yaml").read_text()
    for variant in module.VARIANTS:
        text = (stack.ROOT / variant.collector_config).read_text()
        assert (text == minimal) == (variant.name == "minimal")


def test_cases_sit_outside_the_validate_glob():
    for variant in module.VARIANTS[1:]:
        assert "/config/cases/" in variant.collector_config


def test_assess_reads_token_line_and_suggestion():
    text = "2026-10-06T10:00:00Z error: yaml: line 12: did not find\nDid you mean x?\n"
    result = module.assess("missing", text)
    assert result["names_token"] is False
    assert result["line_number"] is True
    assert result["suggestion"] is True
    assert result["output_lines"] == 2


def test_the_digest_ignores_timestamps():
    first = module.digest("2026-10-06T10:00:00.1Z error")
    second = module.digest("2026-10-07T11:22:33.9Z error")
    assert first == second


def test_no_token_means_not_applicable():
    assert module.assess(None, "")["names_token"] is None


def run(exit_code, output):
    return {"sending": {"exit_code": exit_code, "output": output, "seconds": 1.0}}


def test_validity_rules():
    variant = module.VARIANTS[0]
    assert module.validate_run(variant, run(0, "")) == []
    assert module.validate_run(variant, run(1, "Error: x")) == []
    assert module.validate_run(variant, run(125, "x")) == ["validate exited 125"]
    assert module.validate_run(variant, run(1, "")) == [
        "validate failed without any output"
    ]
    assert "the Docker daemon refused to run the container" in module.validate_run(
        variant, run(1, "Error response from daemon: no")
    )


def test_compose_progress_lines_are_not_collector_output():
    stderr = (
        " Network opentelemetry-experiments_default Creating \n"
        " Container opentelemetry-experiments-collector-run-d172cf9f593f Created \n"
        "Error: service::pipelines::logs: must have at least one receiver\n"
    )
    output = module.output_of({"stdout": "", "stderr": stderr})
    assert (
        output == "Error: service::pipelines::logs: must have at least one receiver\n"
    )
