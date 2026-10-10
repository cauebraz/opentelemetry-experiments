import subprocess

import pytest

from opentelemetry_experiments.core import stack
from opentelemetry_experiments.core.runner import (
    DIRECTORY_PATTERN,
    Column,
    Question,
    Variant,
    accepted_total,
    front_matter,
    invalid_runs,
    lint_question_file,
    question_directories,
    render_question,
    summarize,
)

COLUMNS = (
    Column("HTTP status", "sending.http_status"),
    Column("Accepted spans", "counters.collector.traces.receiver_accepted"),
    Column("Response", "sending.response_body", kind="text"),
)


def run(variant, repetition, status, accepted, body, ok=True):
    return {
        "variant": variant,
        "description": "a span",
        "repetition": repetition,
        "sending": {"http_status": status, "response_body": body},
        "counters": {"collector": {"traces": {"receiver_accepted": accepted}}},
        "log": {"lines": 10, "warn_lines": 0, "error_lines": 0},
        "measured": {},
        "validity": {
            "ok": ok,
            "notes": [] if ok else ["the collector accepted nothing"],
        },
    }


@pytest.fixture
def payload():
    return {
        "question": {
            "id": "L0-01",
            "title": "What happens to a span on the wire?",
            "level": 0,
            "theme": "traces",
            "signals": ["traces"],
        },
        "stack": {"otelcol_contrib": "0.162.0", "prometheus": "v3.15.0"},
        "repetitions": 2,
        "variants": [
            {"name": "valid", "description": "a complete span"},
            {"name": "short-trace-id", "description": "a 15 byte trace id"},
        ],
        "runs": [
            run("valid", 1, 200, 1, "{}"),
            run("valid", 2, 200, 1, "{}"),
            run("short-trace-id", 1, 400, 0, "bad request"),
            run("short-trace-id", 2, 400, 0, "bad request", ok=False),
        ],
    }


@pytest.fixture
def question():
    return Question(
        id="L0-01",
        title="What happens to a span on the wire?",
        level=0,
        theme="traces",
        signals=("traces",),
        variants=(
            Variant("valid", "a complete span"),
            Variant("short-trace-id", "a 15 byte trace id", expects_acceptance=False),
        ),
        send=lambda variant: {},
        columns=COLUMNS,
        interpretation="The receiver rejects the short id before any pipeline runs.",
        does_not_say="Nothing about gRPC, which encodes the id as bytes.",
    )


def test_summarize_uses_only_the_valid_repetitions(payload):
    summary = summarize(payload, COLUMNS)

    assert summary["valid"]["valid_runs"] == 2
    assert summary["short-trace-id"]["valid_runs"] == 1
    assert summary["valid"]["cells"]["sending.http_status"]["median"] == 200
    assert (
        summary["short-trace-id"]["cells"]["sending.response_body"]["value"]
        == "bad request"
    )


def test_invalid_runs_are_listed_with_their_reason(payload):
    failures = invalid_runs(payload)

    assert [run["variant"] for run in failures] == ["short-trace-id"]
    assert failures[0]["validity"]["notes"] == ["the collector accepted nothing"]


def test_render_puts_the_measurement_before_the_interpretation(payload, question):
    rendered = render_question(payload, question)

    assert rendered.startswith("# L0-01 What happens to a span on the wire?")
    assert rendered.index("## Measured") < rendered.index("## Interpretation")
    assert "| `valid` | 200 | 1 | {} |" in rendered
    assert "| `short-trace-id` | 400 | 0 | bad request |" in rendered
    assert "## Invalid runs" in rendered
    assert "Nothing about gRPC" in rendered


def test_render_says_do_not_edit_by_hand(payload, question):
    assert "Do not edit by hand." in render_question(payload, question)


def test_accepted_total_sums_every_signal_and_ignores_missing_counters():
    observed = {
        "collector": {
            "traces": {"receiver_accepted": 2, "receiver_refused": 1},
            "logs": {"receiver_accepted": None},
            "metrics": {"receiver_accepted": 5},
        }
    }

    assert accepted_total(observed) == 7


def test_front_matter_reads_the_fenced_block_only():
    text = '---\nid: L0-01\ntitle: "A question"\n---\n\n## Question\n\nid: not this\n'

    assert front_matter(text) == {"id": "L0-01", "title": "A question"}
    assert front_matter("## Question\n") == {}


@pytest.mark.parametrize(
    ("name", "matches"),
    [
        ("L0-01-span-on-the-wire", True),
        ("L4-05-remote-write-vs-scrape", True),
        ("_template", False),
        ("L0-1-span", False),
        ("L0-01-Span", False),
    ],
)
def test_directory_pattern_accepts_only_the_question_shape(name, matches):
    assert bool(DIRECTORY_PATTERN.match(name)) is matches


def test_lint_reports_a_missing_field_and_a_missing_section(tmp_path):
    directory = tmp_path / "L0-09-example"
    directory.mkdir()
    path = directory / "QUESTION.md"
    path.write_text("---\nid: L0-09\nlevel: 0\ntheme: traces\nstatus: stub\n---\n")

    problems = lint_question_file(path)

    assert any("missing `title`" in problem for problem in problems)
    assert any("`## Question` is missing" in problem for problem in problems)


def test_lint_requires_the_full_sections_once_a_question_is_ready(tmp_path):
    directory = tmp_path / "L0-09-example"
    directory.mkdir()
    path = directory / "QUESTION.md"
    path.write_text(
        "---\nid: L0-09\ntitle: A\nlevel: 0\ntheme: traces\nstatus: ready\n---\n"
        "## Question\n## What the documentation says\n"
    )

    problems = lint_question_file(path)

    assert any("`## Hypothesis` is missing" in problem for problem in problems)


def test_lint_catches_an_id_that_does_not_match_the_directory(tmp_path):
    directory = tmp_path / "L0-09-example"
    directory.mkdir()
    path = directory / "QUESTION.md"
    path.write_text("---\nid: L0-08\ntitle: A\nlevel: 0\ntheme: t\nstatus: stub\n---\n")

    assert any(
        "does not match the directory" in problem for problem in problems_of(path)
    )


def problems_of(path):
    return lint_question_file(path)


LAB = "https://github.com/o/lab"
SHA = "a" * 40
SECTIONS = (
    "## Question\n## What the documentation says\n## Hypothesis\n## How to run\n"
    "## What is measured\n## Result\n## What this does not say\n"
)


def measured_question_file(tmp_path, extra=""):
    directory = tmp_path / "L3-09-example"
    directory.mkdir()
    path = directory / "QUESTION.md"
    path.write_text(
        "---\nid: L3-09\ntitle: A\nlevel: 3\ntheme: t\nstatus: measured\n"
        f"{extra}---\n{SECTIONS}"
    )
    return path


def test_a_measured_question_without_local_results_fails_the_lint(tmp_path):
    problems = problems_of(measured_question_file(tmp_path))

    assert any("`question.py` does not exist" in problem for problem in problems)
    assert any("`results/results.json` does not exist" in p for p in problems)


def test_external_evidence_pinned_to_a_commit_passes_the_lint(tmp_path):
    extra = (
        f"evidence_repository: {LAB}\nevidence_commit: {SHA}\n"
        f"evidence_results: {LAB}/blob/{SHA}/results/results.json\n"
    )

    assert problems_of(measured_question_file(tmp_path, extra)) == []


def test_external_evidence_on_a_branch_fails_the_lint(tmp_path):
    extra = (
        f"evidence_repository: {LAB}\nevidence_commit: main\n"
        f"evidence_results: {LAB}/blob/main/results/results.json\n"
    )

    problems = problems_of(measured_question_file(tmp_path, extra))

    assert any("not a 40 character commit" in problem for problem in problems)


def test_a_results_url_at_another_ref_fails_the_lint(tmp_path):
    extra = (
        f"evidence_repository: {LAB}\nevidence_commit: {SHA}\n"
        f"evidence_results: {LAB}/blob/main/results/results.json\n"
    )

    problems = problems_of(measured_question_file(tmp_path, extra))

    assert any("is not pinned" in problem for problem in problems)


def test_external_evidence_missing_a_field_fails_the_lint(tmp_path):
    extra = f"evidence_repository: {LAB}\nevidence_commit: {SHA}\n"

    problems = problems_of(measured_question_file(tmp_path, extra))

    assert problems == [
        f"{tmp_path}/L3-09-example/QUESTION.md: external evidence is missing "
        "`evidence_results`"
    ]


def test_every_question_directory_in_the_repository_passes_the_lint():
    directories = question_directories()

    assert directories, "the ladder has no question directories"
    problems = [
        problem
        for directory in directories
        for problem in lint_question_file(directory / "QUESTION.md")
    ]
    assert problems == []


def test_the_template_exists_and_carries_every_section():
    text = (stack.QUESTIONS / "_template" / "QUESTION.md").read_text(encoding="utf-8")

    for heading in ("## Question", "## Hypothesis", "## Result"):
        assert heading in text


def test_a_send_that_raises_becomes_an_invalid_run_and_the_stack_is_stopped(
    monkeypatch, tmp_path, question
):
    from opentelemetry_experiments.core import runner

    calls = []
    monkeypatch.setattr(stack, "start", lambda env: calls.append("start"))
    monkeypatch.setattr(stack, "stop", lambda env: calls.append("stop"))
    monkeypatch.setattr(stack, "logs", lambda service, env: f"{service} log")
    monkeypatch.setattr(runner, "counters", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())

    def failing_send(variant):
        raise ConnectionError("refused")

    broken = Question(
        **{**question.__dict__, "send": failing_send, "measure": lambda *a: {"x": 1}}
    )

    observation = runner.run_once(broken, broken.variants[0], 1, tmp_path)

    assert observation["validity"]["ok"] is False
    assert observation["validity"]["notes"][0] == (
        "send failed: ConnectionError: refused"
    )
    assert observation["sending"] == {"error": "ConnectionError: refused"}
    assert observation["measured"] == {}
    assert calls == ["start", "stop"]
    assert (tmp_path / "valid-r1.log").read_text() == "collector log\n"
    assert (tmp_path / "valid-r1-sink.log").read_text() == "sink log\n"


def test_measure_reads_both_logs(monkeypatch, tmp_path, question):
    from opentelemetry_experiments.core import runner

    monkeypatch.setattr(stack, "start", lambda env: None)
    monkeypatch.setattr(stack, "stop", lambda env: None)
    monkeypatch.setattr(stack, "logs", lambda service, env: f"{service} log")
    monkeypatch.setattr(runner, "counters", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())

    def measure(variant, sending, collector_log, sink_log):
        return {"collector": collector_log, "sink": sink_log}

    instrumented = Question(**{**question.__dict__, "measure": measure})

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["measured"] == {
        "collector": "collector log",
        "sink": "sink log",
    }


def test_measure_failure_invalidates_one_run(monkeypatch, tmp_path, question):
    from opentelemetry_experiments.core import runner

    monkeypatch.setattr(stack, "start", lambda env: None)
    monkeypatch.setattr(stack, "stop", lambda env: None)
    monkeypatch.setattr(stack, "logs", lambda service, env: f"{service} log")
    monkeypatch.setattr(runner, "counters", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())

    def failing_measure(*args):
        raise ValueError("unreadable evidence")

    instrumented = Question(**{**question.__dict__, "measure": failing_measure})

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["validity"]["ok"] is False
    assert observation["validity"]["notes"][0] == (
        "measure failed: ValueError: unreadable evidence"
    )
    assert observation["measured"] == {}


def quiet_stack(monkeypatch):
    from opentelemetry_experiments.core import runner

    monkeypatch.setattr(stack, "start", lambda env: None)
    monkeypatch.setattr(stack, "stop", lambda env: None)
    monkeypatch.setattr(stack, "logs", lambda service, env: f"{service} log")
    monkeypatch.setattr(
        runner,
        "counters",
        lambda *args, **kwargs: {"traces": {"receiver_accepted": 1}},
    )
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())
    return runner


def measured_question(question, measure, validate_run=None):
    return Question(
        **{
            **question.__dict__,
            "send": lambda variant: {"http_status": 200},
            "measure": measure,
            "columns": (Column("Found", "measured.found"),),
            "validate_run": validate_run,
        }
    )


def test_a_column_path_that_was_never_recorded_invalidates_the_run(
    monkeypatch, tmp_path, question
):
    runner = quiet_stack(monkeypatch)
    instrumented = measured_question(question, lambda *args: {"other": 1})

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["validity"]["ok"] is False
    assert observation["validity"]["notes"] == ["evidence missing: `measured.found`"]


def test_a_column_recorded_as_none_is_a_valid_absent_metric(
    monkeypatch, tmp_path, question
):
    runner = quiet_stack(monkeypatch)
    instrumented = measured_question(question, lambda *args: {"found": None})

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["validity"] == {"ok": True, "notes": []}


def test_validate_run_notes_invalidate_the_run(monkeypatch, tmp_path, question):
    runner = quiet_stack(monkeypatch)
    instrumented = measured_question(
        question,
        lambda *args: {"found": 3},
        validate_run=lambda variant, run: (
            [] if run["measured"]["found"] == 4 else ["found 3 of 4"]
        ),
    )

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["validity"] == {"ok": False, "notes": ["found 3 of 4"]}


def test_a_validate_run_that_raises_invalidates_the_run(
    monkeypatch, tmp_path, question
):
    runner = quiet_stack(monkeypatch)

    def broken(variant, run):
        raise KeyError("sequence")

    instrumented = measured_question(
        question, lambda *args: {"found": 1}, validate_run=broken
    )

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["validity"]["notes"] == [
        "validate_run failed: KeyError: 'sequence'"
    ]


def test_run_question_records_version_provenance_and_config_hashes(
    monkeypatch, tmp_path, question
):
    import json

    from opentelemetry_experiments.core import runner

    monkeypatch.setattr(runner, "compose_version", lambda: "2.99.0")
    monkeypatch.setattr(
        runner,
        "run_once",
        lambda q, v, r, d, p: run(v.name, r, 200, 1, "{}") | {"position": p},
    )

    payload = runner.run_question(question, 1, tmp_path)

    assert payload["results_version"] == runner.RESULTS_VERSION
    assert payload["provenance"]["docker_compose"] == "2.99.0"
    assert len(payload["provenance"]["compose_sha256"]) == 64
    assert len(payload["provenance"]["uv_lock_sha256"]) == 64
    hashes = payload["variants"][0]["config_sha256"]
    assert len(hashes["collector"]) == 64
    assert len(hashes["sink"]) == 64
    assert payload["variants"][0]["options"] == {}
    assert json.loads((tmp_path / "results.json").read_text()) == payload
    assert f"Results version {runner.RESULTS_VERSION}" in render_question(
        payload, question
    )


def test_an_unversioned_result_file_still_renders(payload, question):
    rendered = render_question(payload, question)

    assert "Results version" not in rendered
    assert "## Measured" in rendered


def test_a_version_2_result_without_lock_hash_still_renders(payload, question):
    payload["results_version"] = 2
    payload["provenance"] = {
        "python": "3.14.8",
        "system": "Darwin",
        "machine": "arm64",
        "docker_compose": "5.5.1",
        "compose_sha256": "a" * 64,
        "dockerfile_sha256": "b" * 64,
    }

    rendered = render_question(payload, question)

    assert "Results version 2" in rendered
    assert "## Measured" in rendered


def test_any_invalid_repetition_fails_the_command(payload):
    from opentelemetry_experiments.run import exit_code

    assert exit_code(payload) == 1
    payload["runs"] = [run for run in payload["runs"] if run["validity"]["ok"]]
    assert exit_code(payload) == 0
    payload["runs"] = []
    assert exit_code(payload) == 1


def test_variant_environment_reaches_stack_start(monkeypatch, tmp_path, question):
    from opentelemetry_experiments.core import runner

    environments = []
    monkeypatch.setattr(stack, "start", environments.append)
    monkeypatch.setattr(stack, "stop", lambda env: None)
    monkeypatch.setattr(stack, "logs", lambda service, env: f"{service} log")
    monkeypatch.setattr(runner, "counters", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())
    variant = Variant(
        "valid",
        "a complete span",
        options={"environment": {"SERVICE_NAME": "from-the-variant"}},
    )
    instrumented = Question(**{**question.__dict__, "variants": (variant,)})

    runner.run_once(instrumented, variant, 1, tmp_path)

    assert environments[0]["SERVICE_NAME"] == "from-the-variant"


def test_variant_input_directory_reaches_stack_start(monkeypatch, tmp_path, question):
    from opentelemetry_experiments.core import runner

    environments = []
    monkeypatch.setattr(stack, "start", environments.append)
    monkeypatch.setattr(stack, "stop", lambda env: None)
    monkeypatch.setattr(stack, "logs", lambda service, env: f"{service} log")
    monkeypatch.setattr(runner, "counters", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())
    variant = Variant(
        "valid",
        "a complete span",
        input_directory="./questions/L2-04-filelog-unparsed-lines/input",
    )
    instrumented = Question(**{**question.__dict__, "variants": (variant,)})

    runner.run_once(instrumented, variant, 1, tmp_path)

    assert (
        environments[0]["COLLECTOR_INPUT_DIR"]
        == "./questions/L2-04-filelog-unparsed-lines/input"
    )


def test_a_stack_that_never_starts_is_one_invalid_run(monkeypatch, tmp_path, question):
    from opentelemetry_experiments.core import runner

    def start(env):
        raise TimeoutError("http://127.0.0.1:23133 was not ready after 60s")

    stopped = []
    monkeypatch.setattr(stack, "start", start)
    monkeypatch.setattr(stack, "stop", stopped.append)
    variant = Variant("valid", "a complete span")

    run = runner.run_once(question, variant, 1, tmp_path)

    assert run["validity"]["ok"] is False
    assert "never became healthy" in run["validity"]["notes"][0]
    assert run["measured"] == {}
    assert len(stopped) == 1


def test_log_collection_failure_invalidates_the_run(monkeypatch, tmp_path, question):
    from opentelemetry_experiments.core import runner

    monkeypatch.setattr(stack, "start", lambda env: None)
    monkeypatch.setattr(stack, "stop", lambda env: None)

    def logs(service, env):
        if service == "sink":
            raise subprocess.CalledProcessError(1, ["docker"], stderr="not found")
        return "collector log"

    monkeypatch.setattr(stack, "logs", logs)
    monkeypatch.setattr(runner, "counters", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "wait_until_stable", lambda sample, **kwargs: sample())
    instrumented = Question(
        **{**question.__dict__, "measure": lambda *args: {"read": True}}
    )

    observation = runner.run_once(instrumented, instrumented.variants[0], 1, tmp_path)

    assert observation["validity"]["ok"] is False
    assert observation["validity"]["notes"][0] == (
        "sink log collection failed: not found"
    )
    assert observation["measured"] == {}


def test_new_question_fills_every_placeholder_in_both_files(tmp_path):
    import shutil

    from opentelemetry_experiments.new_question import create

    shutil.copytree(stack.QUESTIONS / "_template", tmp_path / "_template")

    created = create(tmp_path, "l1-07", "some-slug", "logs")

    assert created == tmp_path / "L1-07-some-slug"
    for name in ("QUESTION.md", "question.py"):
        text = (created / name).read_text()
        assert "L0-00" not in text, name
    assert "id: L1-07" in (created / "QUESTION.md").read_text()
    assert "level: 1" in (created / "QUESTION.md").read_text()
    assert "theme: logs" in (created / "QUESTION.md").read_text()
    code = (created / "question.py").read_text()
    assert 'id="L1-07"' in code
    assert "level=1" in code
    assert 'theme="logs"' in code
    assert "./questions/L1-07-some-slug/config/collector.yaml" in code
    assert (created / "config" / "collector.yaml").exists()
    assert lint_question_file(created / "QUESTION.md") == []


def test_new_question_refuses_a_bad_shape_or_theme(tmp_path):
    import shutil

    from opentelemetry_experiments.new_question import create

    shutil.copytree(stack.QUESTIONS / "_template", tmp_path / "_template")

    with pytest.raises(ValueError):
        create(tmp_path, "L1-7", "slug", "logs")
    with pytest.raises(ValueError):
        create(tmp_path, "L1-07", "slug", "dashboards")


def test_variants_run_in_declared_order_unless_rotation_is_asked():
    from opentelemetry_experiments.core.runner import Variant, execution_order

    variants = [Variant(name=name, description=name) for name in "ABCD"]

    def names(repetition, rotate):
        return "".join(v.name for v in execution_order(variants, repetition, rotate))

    assert [names(r, False) for r in range(1, 5)] == ["ABCD"] * 4
    assert [names(r, True) for r in range(1, 5)] == ["ABCD", "BCDA", "CDAB", "DABC"]
    assert names(5, True) == "ABCD"


def test_a_rotated_question_records_order_and_positions(
    monkeypatch, tmp_path, question
):
    import dataclasses

    from opentelemetry_experiments.core import runner

    rotated = dataclasses.replace(
        question,
        variants=(
            question.variants[0],
            dataclasses.replace(question.variants[0], name="second"),
        ),
        rotate_variants=True,
    )
    monkeypatch.setattr(runner, "compose_version", lambda: None)
    monkeypatch.setattr(
        runner,
        "run_once",
        lambda q, v, r, d, p: run(v.name, r, 200, 1, "{}") | {"position": p},
    )

    payload = runner.run_question(rotated, 2, tmp_path)

    first = question.variants[0].name
    assert payload["execution_order"] == [[first, "second"], ["second", first]]
    assert [entry["position"] for entry in payload["runs"]] == [1, 2, 1, 2]


def test_a_question_without_a_stack_never_starts_one(monkeypatch, tmp_path, question):
    import dataclasses

    from opentelemetry_experiments.core import runner

    def refuse(*args, **kwargs):
        raise AssertionError("the stack was started")

    monkeypatch.setattr(runner.stack, "start", refuse)
    static = dataclasses.replace(
        question,
        starts_stack=False,
        send=lambda variant: {"exit_code": 0},
        columns=(Column("Exit code", "sending.exit_code"),),
    )

    run = runner.run_once(static, static.variants[0], 1, tmp_path, 3)

    assert run["validity"] == {"ok": True, "notes": []}
    assert run["position"] == 3
    assert run["sending"] == {"exit_code": 0}


def test_a_failed_send_without_a_stack_is_invalid(tmp_path, question):
    import dataclasses

    from opentelemetry_experiments.core import runner

    def broken(variant):
        raise OSError("docker is missing")

    static = dataclasses.replace(question, starts_stack=False, send=broken)
    run = runner.run_once(static, static.variants[0], 1, tmp_path)

    assert run["validity"]["ok"] is False
    assert run["validity"]["notes"] == ["send failed: OSError: docker is missing"]
