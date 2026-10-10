import hashlib
import importlib.util
import json
import platform
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentelemetry_experiments.core import report, stack
from opentelemetry_experiments.core.prometheus import counters, wait_until_stable

PROM = stack.PROMETHEUS_URL

DIRECTORY_PATTERN = re.compile(
    r"^L(?P<level>\d)-(?P<number>\d\d)-(?P<slug>[a-z0-9-]+)$"
)
FRONT_MATTER = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)

REQUIRED_FIELDS = ("id", "title", "level", "theme", "status")
STATUSES = ("stub", "ready", "measured")

QUIET_SETTLE = 10.0
RESULTS_VERSION = 2

STUB_SECTIONS = ("## Question", "## What the documentation says")
FULL_SECTIONS = (
    *STUB_SECTIONS,
    "## Hypothesis",
    "## How to run",
    "## What is measured",
    "## Result",
    "## What this does not say",
)


@dataclass(frozen=True)
class Variant:
    """One setting of the independent variable, with the stack it needs.

    Two validity rules, neither of them a prediction. `expects_telemetry` says
    whether the collector under test publishes any `otelcol_*` counter for this
    variant, and decides how long an all-zero reading is waited out before it is
    believed. `expects_acceptance` says whether `otelcol_receiver_accepted_*`
    should rise, which is false for a payload the receiver refuses.
    """

    name: str
    description: str
    collector_config: str = stack.COLLECTOR_DEBUG_CONFIG
    sink_config: str = stack.SINK_CONFIG
    prometheus_config: str = stack.PROMETHEUS_CONFIG
    input_directory: str = stack.COLLECTOR_INPUT_DIR
    collector_memory_limit: str = stack.NO_MEMORY_LIMIT
    gateway_config: str = ""
    collector_build_context: str = "."
    collector_dockerfile: str = "Dockerfile.collector"
    collector_image: str = ""
    expects_acceptance: bool = True
    expects_telemetry: bool = True
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Column:
    """One dependent variable as it appears in the table."""

    label: str
    path: str
    kind: str = "number"
    digits: int = 0


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    level: int
    theme: str
    signals: tuple[str, ...]
    variants: tuple[Variant, ...]
    send: Callable[[Variant], dict[str, Any]]
    columns: tuple[Column, ...]
    interpretation: str
    does_not_say: str
    measure: Callable[[Variant, Mapping[str, Any], str, str], dict[str, Any]] | None = (
        None
    )
    validate_run: Callable[[Variant, Mapping[str, Any]], Sequence[str]] | None = None
    repetitions: int = 3
    rotate_variants: bool = False
    starts_stack: bool = True


def front_matter(text: str) -> dict[str, str]:
    match = FRONT_MATTER.match(text)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip().strip('"')
    return fields


def lint_question_file(path: Path) -> list[str]:
    """Problems with one QUESTION.md, as sentences naming the file."""
    text = path.read_text(encoding="utf-8")
    fields = front_matter(text)
    problems = [
        f"{path}: front matter is missing `{name}`"
        for name in REQUIRED_FIELDS
        if not fields.get(name)
    ]
    status = fields.get("status", "")
    if status and status not in STATUSES:
        problems.append(f"{path}: status `{status}` is not one of {list(STATUSES)}")
    directory = DIRECTORY_PATTERN.match(path.parent.name)
    if directory is None:
        problems.append(
            f"{path.parent}: the directory is not named L<level>-<nn>-<slug>"
        )
    elif fields.get("id") and fields["id"] != path.parent.name[:5]:
        problems.append(
            f"{path}: front matter id `{fields['id']}` does not match the directory"
        )
    if status == "measured":
        problems.extend(evidence_lint(path, fields))
    expected = FULL_SECTIONS if status != "stub" else STUB_SECTIONS
    problems.extend(
        f"{path}: section `{heading}` is missing"
        for heading in expected
        if heading not in text
    )
    return problems


EXTERNAL_FIELDS = ("evidence_repository", "evidence_commit", "evidence_results")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def evidence_lint(path: Path, fields: Mapping[str, str]) -> list[str]:
    """A measured question has local results, or external ones pinned to a commit."""
    present = [name for name in EXTERNAL_FIELDS if fields.get(name)]
    if not present:
        missing = [
            name
            for name in ("question.py", "results/results.json")
            if not (path.parent / name).is_file()
        ]
        return [f"{path}: measured but `{name}` does not exist" for name in missing]
    problems = [
        f"{path}: external evidence is missing `{name}`"
        for name in EXTERNAL_FIELDS
        if name not in present
    ]
    if problems:
        return problems
    repository = fields["evidence_repository"].rstrip("/")
    commit = fields["evidence_commit"]
    if not COMMIT.match(commit):
        problems.append(f"{path}: `evidence_commit` is not a 40 character commit")
    for url in fields["evidence_results"].split():
        if not url.startswith(f"{repository}/blob/{commit}/"):
            problems.append(
                f"{path}: `{url}` is not pinned to {repository} at `evidence_commit`"
            )
    return problems


def question_directories(root: Path | None = None) -> list[Path]:
    base = stack.QUESTIONS if root is None else root
    return sorted(
        path
        for path in base.iterdir()
        if path.is_dir() and DIRECTORY_PATTERN.match(path.name)
    )


def directory_for(question_id: str, root: Path | None = None) -> Path:
    wanted = question_id.upper()
    for path in question_directories(root):
        if path.name.startswith(f"{wanted}-"):
            return path
    raise ValueError(f"no question directory starts with {wanted}")


def load_question(directory: Path) -> Question:
    module_path = directory / "question.py"
    if not module_path.exists():
        raise ValueError(f"{directory.name} has no question.py, it is still a stub")
    spec = importlib.util.spec_from_file_location(
        f"opentelemetry_experiments.questions.{directory.name.replace('-', '_')}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"{module_path} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.QUESTION


def accepted_total(
    observed: Mapping[str, Any], role: str = "receiver_accepted"
) -> float:
    collector = observed.get("collector", {})
    return sum(
        value
        for signal in collector.values()
        for key, value in signal.items()
        if key == role and value is not None
    )


def run_once(
    question: Question,
    variant: Variant,
    repetition: int,
    log_directory: Path,
    position: int = 1,
) -> dict[str, Any]:
    if not question.starts_stack:
        return run_without_stack(question, variant, repetition, position)
    extra = variant.options.get("environment")
    if extra is not None and not isinstance(extra, Mapping):
        raise TypeError("variant.options['environment'] must be a mapping")
    feature_gates = variant.options.get("feature_gates", "")
    if feature_gates is not None and not isinstance(feature_gates, str):
        raise TypeError("variant.options['feature_gates'] must be a string")
    environment = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
        prometheus_config=variant.prometheus_config,
        input_directory=variant.input_directory,
        extra=extra,
        collector_memory_limit=variant.collector_memory_limit,
        collector_feature_gates=feature_gates or "",
        gateway_config=variant.gateway_config,
        collector_build_context=variant.collector_build_context,
        collector_dockerfile=variant.collector_dockerfile,
        collector_image=variant.collector_image,
    )
    notes: list[str] = []
    try:
        stack.start(environment)
    except (TimeoutError, subprocess.CalledProcessError) as error:
        stack.stop(environment)
        return {
            "variant": variant.name,
            "description": variant.description,
            "repetition": repetition,
            "position": position,
            "sending": {"error": f"{type(error).__name__}: {error}"},
            "counters": {},
            "log": stack.log_summary(""),
            "measured": {},
            "validity": {
                "ok": False,
                "notes": [f"the stack never became healthy: {error}"],
            },
        }
    try:
        try:
            sending = question.send(variant)
        except Exception as error:
            sending = {"error": f"{type(error).__name__}: {error}"}
            notes.append(f"send failed: {type(error).__name__}: {error}")

        def sample() -> dict[str, Any]:
            observed_roles = {
                "collector": counters(PROM, "collector", question.signals),
                "sink": counters(PROM, "sink", question.signals),
            }
            if stack.gateway_enabled(environment):
                observed_roles["gateway"] = counters(PROM, "gateway", question.signals)
            return observed_roles

        try:
            observed = wait_until_stable(
                sample,
                require_nonzero=variant.expects_telemetry,
                minimum_seconds=0.0 if variant.expects_telemetry else QUIET_SETTLE,
            )
            settled = True
        except TimeoutError as error:
            observed = sample()
            settled = False
            notes.append(str(error))

        def read_log(service: str) -> str:
            try:
                text = stack.logs(service, environment)
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or "").strip() or f"exit {error.returncode}"
                notes.append(f"{service} log collection failed: {detail}")
                return ""
            if not text.strip():
                notes.append(f"{service} log is empty")
            return text

        collector_log = read_log("collector")
        sink_log = read_log("sink")
        stack.save_logs(
            log_directory / f"{variant.name}-r{repetition}.log",
            collector_log,
        )
        stack.save_logs(
            log_directory / f"{variant.name}-r{repetition}-sink.log",
            sink_log,
        )
        if stack.gateway_enabled(environment):
            gateway_log = read_log("gateway")
            stack.save_logs(
                log_directory / f"{variant.name}-r{repetition}-gateway.log",
                gateway_log,
            )
        measured = {}
        can_measure = (
            question.measure is not None
            and "error" not in sending
            and collector_log.strip()
            and sink_log.strip()
        )
        if can_measure:
            try:
                measured = question.measure(
                    variant,
                    sending,
                    collector_log,
                    sink_log,
                )
            except Exception as error:
                notes.append(f"measure failed: {type(error).__name__}: {error}")
        accepted = accepted_total(observed)
        if variant.expects_acceptance and accepted <= 0:
            notes.append("the collector accepted nothing, but the variant expected it")
        if not variant.expects_acceptance and accepted > 0:
            notes.append(f"the collector accepted {accepted:g} items it should refuse")
        observation = {
            "variant": variant.name,
            "description": variant.description,
            "repetition": repetition,
            "position": position,
            "sending": sending,
            "counters": observed,
            "log": stack.log_summary(collector_log),
            "measured": measured,
        }
        if settled and not notes:
            notes.extend(evidence_problems(question, variant, observation))
        observation["validity"] = {"ok": settled and not notes, "notes": notes}
        return observation
    finally:
        stack.stop(environment)


def run_without_stack(
    question: Question,
    variant: Variant,
    repetition: int,
    position: int,
) -> dict[str, Any]:
    """For a question whose `send` runs its own containers, such as `validate`."""
    notes: list[str] = []
    try:
        sending = question.send(variant)
    except Exception as error:
        sending = {"error": f"{type(error).__name__}: {error}"}
        notes.append(f"send failed: {type(error).__name__}: {error}")
    measured: dict[str, Any] = {}
    if question.measure is not None and "error" not in sending:
        try:
            measured = question.measure(variant, sending, "", "")
        except Exception as error:
            notes.append(f"measure failed: {type(error).__name__}: {error}")
    observation = {
        "variant": variant.name,
        "description": variant.description,
        "repetition": repetition,
        "position": position,
        "sending": sending,
        "counters": {},
        "log": stack.log_summary(""),
        "measured": measured,
    }
    if not notes:
        notes.extend(evidence_problems(question, variant, observation))
    observation["validity"] = {"ok": not notes, "notes": notes}
    return observation


def evidence_problems(
    question: Question,
    variant: Variant,
    observation: Mapping[str, Any],
) -> list[str]:
    """A column whose path is absent was never read, unlike one recorded as None."""
    problems = [
        f"evidence missing: `{column.path}`"
        for column in question.columns
        if not report.has(observation, column.path)
    ]
    if problems or question.validate_run is None:
        return problems
    try:
        return [str(note) for note in question.validate_run(variant, observation)]
    except Exception as error:
        return [f"validate_run failed: {type(error).__name__}: {error}"]


def file_sha256(relative: str) -> str | None:
    path = stack.ROOT / relative
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose_version() -> str | None:
    try:
        result = stack.command("docker", "compose", "version", "--short")
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None


def provenance() -> dict[str, Any]:
    """What a rerun must match; no clock reading, so a rerun can be diffed."""
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "docker_compose": compose_version(),
        "compose_sha256": file_sha256("compose.yaml"),
        "dockerfile_sha256": file_sha256("Dockerfile.collector"),
    }


def variant_record(variant: Variant) -> dict[str, Any]:
    configs = {
        "collector": file_sha256(variant.collector_config),
        "sink": file_sha256(variant.sink_config),
        "prometheus": file_sha256(variant.prometheus_config),
    }
    if variant.gateway_config:
        configs["gateway"] = file_sha256(variant.gateway_config)
    return {
        "name": variant.name,
        "description": variant.description,
        "options": json.loads(json.dumps(variant.options, default=str)),
        "collector_memory_limit": variant.collector_memory_limit,
        "gateway_config": variant.gateway_config,
        "config_sha256": configs,
    }


def execution_order(
    variants: Sequence[Variant], repetition: int, rotate: bool
) -> list[Variant]:
    """With rotation, repetition r starts at variant r, a Latin square."""
    if not rotate:
        return list(variants)
    shift = (repetition - 1) % len(variants)
    return [*variants[shift:], *variants[:shift]]


def run_question(
    question: Question,
    repetitions: int,
    results_directory: Path,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    orders: list[list[str]] = []
    for repetition in range(1, repetitions + 1):
        order = execution_order(question.variants, repetition, question.rotate_variants)
        orders.append([variant.name for variant in order])
        for position, variant in enumerate(order, start=1):
            print(f"  {question.id} {variant.name} repetition {repetition}")
            run = run_once(
                question, variant, repetition, results_directory / "logs", position
            )
            runs.append(run)
    payload = {
        "results_version": RESULTS_VERSION,
        "provenance": provenance(),
        "question": {
            "id": question.id,
            "title": question.title,
            "level": question.level,
            "theme": question.theme,
            "signals": list(question.signals),
        },
        "stack": {
            "otelcol_contrib": stack.OTELCOL_VERSION,
            "prometheus": stack.PROMETHEUS_VERSION,
        },
        "repetitions": repetitions,
        "execution_order": orders,
        "columns": [
            {"label": column.label, "path": column.path, "kind": column.kind}
            for column in question.columns
        ],
        "variants": [variant_record(variant) for variant in question.variants],
        "runs": runs,
    }
    results_directory.mkdir(parents=True, exist_ok=True)
    (results_directory / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def summarize(payload: Mapping[str, Any], columns: Sequence[Column]) -> dict[str, Any]:
    """Per variant, one summary per column over the valid repetitions."""
    summary: dict[str, Any] = {}
    for variant in payload["variants"]:
        name = variant["name"]
        valid = [
            run
            for run in payload["runs"]
            if run["variant"] == name and run["validity"]["ok"]
        ]
        cells: dict[str, Any] = {}
        for column in columns:
            values = [report.get(run, column.path) for run in valid]
            cells[column.path] = (
                report.stats(values)
                if column.kind == "number"
                else report.agreement(values)
            )
        summary[name] = {
            "description": variant["description"],
            "valid_runs": len(valid),
            "cells": cells,
        }
    return summary


def invalid_runs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [run for run in payload["runs"] if not run["validity"]["ok"]]


def provenance_lines(payload: Mapping[str, Any]) -> list[str]:
    recorded = payload.get("provenance")
    if not recorded:
        return []
    return [
        f"Results version {payload.get('results_version')}, recorded with Python "
        f"{recorded.get('python')} on {recorded.get('system')} "
        f"{recorded.get('machine')} and Docker Compose "
        f"{recorded.get('docker_compose') or 'unknown'}.",
        "",
    ]


def render_question(payload: Mapping[str, Any], question: Question) -> str:
    summary = summarize(payload, question.columns)
    lines = [
        f"# {payload['question']['id']} {payload['question']['title']}",
        "",
        f"Level {payload['question']['level']}, theme "
        f"{payload['question']['theme']}. {payload['repetitions']} "
        f"{'repetition' if payload['repetitions'] == 1 else 'repetitions'} per "
        f"variant on otelcol-contrib {payload['stack']['otelcol_contrib']} and "
        f"Prometheus {payload['stack']['prometheus']}.",
        "",
        *provenance_lines(payload),
        "Rendered from this question's `results.json` by `just render "
        f"{payload['question']['id']}`. Do not edit by hand.",
        "",
        "## Variants",
        "",
    ]
    lines.extend(
        report.table(
            ("Variant", "What it sends"),
            [
                (f"`{name}`", report.escape(body["description"]))
                for name, body in summary.items()
            ],
        )
    )
    lines.extend(["", "## Measured", ""])
    header = ("Variant", *(column.label for column in question.columns))
    rows = []
    for name, body in summary.items():
        row = [f"`{name}`"]
        for column in question.columns:
            cell = body["cells"][column.path]
            row.append(
                report.number_cell(cell, column.digits)
                if column.kind == "number"
                else report.text_cell(cell)
            )
        rows.append(row)
    lines.extend(report.table(header, rows))
    lines.extend(
        [
            "",
            "Numbers are the median across the valid repetitions, with the range in "
            "parentheses when the repetitions disagreed. Text columns show the value "
            "when every repetition agreed.",
            "",
        ]
    )
    failures = invalid_runs(payload)
    if failures:
        lines.extend(["## Invalid runs", ""])
        lines.extend(
            report.table(
                ("Variant", "Repetition", "Why"),
                [
                    (
                        f"`{run['variant']}`",
                        str(run["repetition"]),
                        report.escape("; ".join(run["validity"]["notes"])),
                    )
                    for run in failures
                ],
            )
        )
        lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            question.interpretation.strip(),
            "",
            "## What this does not say",
            "",
            question.does_not_say.strip(),
            "",
        ]
    )
    return "\n".join(lines)
