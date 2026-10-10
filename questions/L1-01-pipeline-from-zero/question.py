"""L1-01: what `otelcol validate` says about a minimal config and nine changes to it."""

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import stack
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L1-01-pipeline-from-zero/config"
LINE_NUMBER = re.compile(r"\bline \d+")
SUGGESTION = re.compile(r"did you mean", re.I)
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
DAEMON_ERROR = "Error response from daemon"
COMPOSE_PROGRESS = re.compile(r"(?m)^\s*(?:Container|Network|Volume) \S+ \w+\s*$\n?")

CASES = (
    ("minimal", "the smallest logs pipeline", None),
    ("no-receivers", "the pipeline lists no receiver", "logs"),
    ("no-exporters", "the pipeline lists no exporter", "logs"),
    ("configured-unused", "an exporter configured and in no pipeline", "unused"),
    ("used-undeclared", "a pipeline exporter that is not configured", "missing"),
    ("unknown-type", "an exporter of type otlp_grcp", "otlp_grcp"),
    ("misspelled-key", "debug has verbosty", "verbosty"),
    ("wrong-type", "debug has sampling_initial: many", "sampling_initial"),
    ("undefined-env", "the endpoint is an undefined env var", "L1_01_UNDEFINED"),
    ("bad-indentation", "the exporters list is mis-indented", None),
)


def config_path(name: str) -> str:
    if name == "minimal":
        return f"{DIRECTORY}/minimal.yaml"
    return f"{DIRECTORY}/cases/{name}.yaml"


VARIANTS = tuple(
    Variant(
        name=name,
        description=description,
        collector_config=config_path(name),
        options={"token": token},
    )
    for name, description, token in CASES
)


def output_of(result: Mapping[str, Any]) -> str:
    """What the Collector printed, without Compose's container progress lines."""
    text = f"{result.get('stdout', '')}{result.get('stderr', '')}"
    return COMPOSE_PROGRESS.sub("", text)


def digest(text: str) -> str:
    return hashlib.sha256(TIMESTAMP.sub("<t>", text).encode()).hexdigest()[:12]


def assess(token: str | None, text: str) -> dict[str, Any]:
    return {
        "names_token": None if token is None else token in text,
        "line_number": bool(LINE_NUMBER.search(text)),
        "suggestion": bool(SUGGESTION.search(text)),
        "output_lines": sum(1 for line in text.splitlines() if line.strip()),
        "output_digest": digest(text),
    }


def send(variant: Variant) -> dict[str, Any]:
    result = stack.validate_config(variant.collector_config)
    return {
        "exit_code": result["exit_code"],
        "seconds": result["seconds"],
        "output": output_of(result),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    return assess(variant.options["token"], sending["output"])


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    notes = []
    if sending["exit_code"] not in (0, 1):
        notes.append(f"validate exited {sending['exit_code']}")
    if DAEMON_ERROR in sending["output"]:
        notes.append("the Docker daemon refused to run the container")
    if sending["exit_code"] != 0 and not sending["output"].strip():
        notes.append("validate failed without any output")
    return notes


QUESTION = Question(
    id="L1-01",
    title=(
        "What is the smallest Collector config that works, "
        "and what does it say when it is wrong?"
    ),
    level=1,
    theme="collector",
    signals=("logs",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    starts_stack=False,
    columns=(
        Column("Exit code", "sending.exit_code"),
        Column("Names token", "measured.names_token", kind="text"),
        Column("Line number", "measured.line_number", kind="text"),
        Column("Suggestion", "measured.suggestion", kind="text"),
        Column("Output lines", "measured.output_lines"),
        Column("Seconds", "sending.seconds", digits=2),
        Column("Output digest", "measured.output_digest", kind="text"),
    ),
    interpretation="""
`validate` is silent about what the documentation calls not enabled. An exporter
configured and listed in no pipeline, and a receiver whose endpoint is an
undefined environment variable, both exited 0 with no output, exactly like the
minimal config. Nothing on the command line separates a correct config from one
that drops a component or an endpoint.

The seven refusals each exited 1 and named the pipeline, component or key at
fault, in one to three lines. Only the YAML error carried a line number, and it
pointed at line 10, the start of the `pipelines` block, six lines above the
mis-indented key. The unknown type printed 57 valid values in a different
order on every run and no suggestion, with `otlp_grpc` among them.

What this does to an alert or a deploy: a CI step that runs `validate` catches
missing, undeclared, unknown and mistyped components, and does not catch a
component left out of `service` or an empty variable. Those need a check of
their own, such as diffing the configured components against the pipelines.
""",
    does_not_say="""
Configs were validated, not started, so an empty endpoint that validates may
still fail at start. Ten cases on one logs pipeline; processors, connectors,
extensions, `--set` and multiple `--config` sources were not run. The messages
are `otelcol-contrib 0.162.0`'s.
""",
)
