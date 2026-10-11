import json

import pytest

from opentelemetry_experiments import render
from opentelemetry_experiments.core import stack


def write_question(root, question_id="L0-01", status="measured", external=False):
    directory = root / f"{question_id}-test"
    directory.mkdir()
    evidence = ""
    if external:
        evidence = (
            "\nevidence_repository: https://example.com/repository"
            "\nevidence_commit: 0123456789012345678901234567890123456789"
            "\nevidence_results: https://example.com/results.json"
        )
    (directory / "QUESTION.md").write_text(
        f"---\nid: {question_id}\nstatus: {status}{evidence}\n---\n",
        encoding="utf-8",
    )
    return directory


def write_question_module(directory, question_id="L0-01"):
    (directory / "question.py").write_text(
        f"""
from opentelemetry_experiments.core.runner import Question, Variant

QUESTION = Question(
    id={question_id!r},
    title="Test question",
    level=0,
    theme="test",
    signals=("traces",),
    variants=(Variant("valid", "a test variant"),),
    send=lambda variant: {{}},
    columns=(),
    interpretation="The result is interpreted.",
    does_not_say="The result is deliberately narrow.",
)
""".lstrip(),
        encoding="utf-8",
    )


def payload(question_id="L0-01", version=None, valid=True):
    recorded = {
        "question": {
            "id": question_id,
            "title": "Test question",
            "level": 0,
            "theme": "test",
            "signals": ["traces"],
        },
        "stack": {"otelcol_contrib": "0.162.0", "prometheus": "v3.15.0"},
        "repetitions": 1,
        "variants": [{"name": "valid", "description": "a test variant"}],
        "runs": [
            {
                "variant": "valid",
                "repetition": 1,
                "validity": {
                    "ok": valid,
                    "notes": [] if valid else ["the run was invalid"],
                },
            }
        ],
    }
    if version is not None:
        recorded["results_version"] = version
        recorded["provenance"] = {
            "python": "3.14.8",
            "system": "Darwin",
            "machine": "arm64",
            "docker_compose": "5.5.1",
            "compose_sha256": "a" * 64,
            "dockerfile_sha256": "b" * 64,
        }
        if version == 3:
            recorded["provenance"]["uv_lock_sha256"] = "c" * 64
    return recorded


def write_results(directory, recorded, report="recorded\n"):
    results = directory / "results"
    results.mkdir()
    (results / "results.json").write_text(
        json.dumps(recorded),
        encoding="utf-8",
    )
    (results / "REPORT.md").write_text(report, encoding="utf-8")


def test_all_canonical_repository_reports_match_their_recorded_results():
    checked, problems = render.check_reports()

    assert checked == 23
    assert problems == []


def test_check_reports_shows_a_diff_and_ignores_nested_history(monkeypatch, tmp_path):
    directory = write_question(tmp_path)
    write_results(directory, {})
    historical = directory / "results" / "historical"
    historical.mkdir()
    (historical / "results.json").write_text("{}", encoding="utf-8")
    (historical / "REPORT.md").write_text("historical\n", encoding="utf-8")
    monkeypatch.setattr(render, "render_recorded_report", lambda directory: "new\n")

    checked, problems = render.check_reports(tmp_path)

    assert checked == 1
    assert len(problems) == 1
    assert "-recorded" in problems[0]
    assert "+new" in problems[0]
    assert "historical" not in problems[0]


def test_check_reports_requires_both_canonical_artifacts(tmp_path):
    directory = write_question(tmp_path)
    results = directory / "results"
    results.mkdir()
    (results / "results.json").write_text("{}", encoding="utf-8")

    checked, problems = render.check_reports(tmp_path)

    assert checked == 0
    assert problems == [f"{results / 'REPORT.md'} does not exist"]


def test_check_reports_reports_invalid_json(tmp_path):
    directory = write_question(tmp_path)
    results = directory / "results"
    results.mkdir()
    (results / "results.json").write_text("{", encoding="utf-8")
    (results / "REPORT.md").write_text("recorded\n", encoding="utf-8")

    checked, problems = render.check_reports(tmp_path)

    assert checked == 1
    assert len(problems) == 1
    assert "Expecting property name" in problems[0]


def test_check_reports_ignores_external_and_unmeasured_questions(tmp_path):
    write_question(tmp_path, "L3-01", external=True)
    write_question(tmp_path, "L4-06", status="ready")

    checked, problems = render.check_reports(tmp_path)

    assert checked == 0
    assert problems == []


def test_render_recorded_report_rejects_a_mismatched_question_id(tmp_path):
    directory = write_question(tmp_path)
    write_question_module(directory)
    write_results(directory, payload(question_id="L9-99"))

    with pytest.raises(ValueError, match="does not match 'L0-01'"):
        render.render_recorded_report(directory)


@pytest.mark.parametrize("version", [None, 2, 3])
def test_render_recorded_report_supports_every_results_version(tmp_path, version):
    directory = write_question(tmp_path)
    write_question_module(directory)
    write_results(directory, payload(version=version, valid=False))

    report = render.render_recorded_report(directory)

    if version is None:
        assert "Results version" not in report
    else:
        assert f"Results version {version}" in report
    assert "## Invalid runs" in report
    assert "the run was invalid" in report


def test_rendered_repository_reports_do_not_mutate_the_worktree():
    reports = sorted((stack.ROOT / "questions").glob("*/results/REPORT.md"))
    before = {
        report: (report.read_bytes(), report.stat().st_mtime_ns) for report in reports
    }

    checked, problems = render.check_reports()

    assert checked == 23
    assert problems == []
    assert {
        report: (report.read_bytes(), report.stat().st_mtime_ns) for report in reports
    } == before
