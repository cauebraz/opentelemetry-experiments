"""L2-04: one log line in ten that the parser rejects, under each on_error."""

import re
import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import debuglog, stack
from opentelemetry_experiments.core.prometheus import query_value, series_names
from opentelemetry_experiments.core.runner import Column, Question, Variant

LINES = 100
UNPARSABLE_EVERY = 10
UNPARSABLE = LINES // UNPARSABLE_EVERY
PARSABLE = LINES - UNPARSABLE

ARRIVAL_TIMEOUT = 30.0

DIRECTORY = "questions/L2-04-filelog-unparsed-lines"
CONFIG = f"./{DIRECTORY}/config"
INPUT_DIRECTORY = f"./{DIRECTORY}/input"
INPUT_FILE = stack.ROOT / DIRECTORY / "input" / "app.log"

FAILED_ENTRY = re.compile(r"Failed to process entry")
EMIT_FAILURE = re.compile(r"failed to emit token")
PARSER_SERIES = re.compile(r"parser|operator|stanza|unparsed")


def variant(
    name: str,
    description: str,
    config: str,
    expected_records: int,
) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{CONFIG}/{config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
        input_directory=INPUT_DIRECTORY,
        options={"expected_records": expected_records},
    )


VARIANTS = (
    variant(
        "default",
        "on_error left unset, which the documentation says is send",
        "collector-default.yaml",
        LINES,
    ),
    variant(
        "send-quiet",
        "on_error send_quiet",
        "collector-send-quiet.yaml",
        LINES,
    ),
    variant(
        "drop",
        "on_error drop",
        "collector-drop.yaml",
        PARSABLE,
    ),
    variant(
        "drop-quiet",
        "on_error drop_quiet",
        "collector-drop-quiet.yaml",
        PARSABLE,
    ),
)


def file_lines() -> list[str]:
    """One line in ten is a truncated JSON fragment the regex cannot match."""
    lines = []
    for index in range(1, LINES + 1):
        if index % UNPARSABLE_EVERY == 0:
            lines.append(f'{{"partial": "json", "n": {index}')
        else:
            clock = f"14:{index // 60:02d}:{index % 60:02d}"
            lines.append(f"{clock} INFO request {index:03d} served")
    return lines


def arrived_at_sink() -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        'sum({__name__=~"otelcol_receiver_accepted_log_records(_total)?", job="sink"})',
    )


def wait_for_arrival(target: float) -> float:
    """Records at the sink once the count reached the target and stopped rising."""
    started = time.monotonic()
    previous: float | None = None
    while time.monotonic() - started < ARRIVAL_TIMEOUT:
        current = arrived_at_sink()
        if current == target and current == previous:
            return current
        previous = current
        time.sleep(0.5)
    raise TimeoutError(
        f"the sink did not settle at {target:g} records after "
        f"{ARRIVAL_TIMEOUT:g}s; last sample was {previous}"
    )


def clear_input() -> None:
    """A run starts with nothing to tail, so the receiver reads only this run."""
    INPUT_FILE.unlink(missing_ok=True)


clear_input()


def send(variant: Variant) -> dict[str, Any]:
    """The file appears once the collector is already polling the directory.

    It is written beside its destination and moved into place, so the receiver
    never reads a half-written file.
    """
    clear_input()
    try:
        INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        staged = INPUT_FILE.with_suffix(".staged")
        staged.write_text("\n".join(file_lines()) + "\n", encoding="utf-8")
        staged.replace(INPUT_FILE)
        settled = wait_for_arrival(variant.options["expected_records"])
        return {
            "http_status": None,
            "lines_written": LINES,
            "unparsable_written": UNPARSABLE,
            "records_settled_at": settled,
        }
    finally:
        clear_input()


def receiver_counter(role: str) -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        f'sum({{__name__=~"otelcol_receiver_{role}_log_records(_total)?",'
        ' job="collector"})',
    )


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    bodies = debuglog.log_bodies(sink_log)
    records = debuglog.log_attributes(sink_log)
    collector = stack.unprefixed(collector_log)
    unparsable = {line for line in file_lines() if line.startswith("{")}
    measured = {
        "receiver_failed": receiver_counter("failed"),
        "records_arrived": len(bodies),
        "records_with_level": sum("level" in record for record in records),
        "raw_bodies_arrived": sum(body in unparsable for body in bodies),
        "requests_at_sink": len(debuglog.export_requests(sink_log, "logs")),
        "failed_entry_lines": len(FAILED_ENTRY.findall(collector)),
        "emit_failure_lines": len(EMIT_FAILURE.findall(collector)),
        "parser_series": len(
            [
                name
                for name in series_names(stack.PROMETHEUS_URL, "collector")
                if PARSER_SERIES.search(name)
            ]
        ),
    }
    return measured


QUESTION = Question(
    id="L2-04",
    title="What happens to the 10% of log lines that do not match the parser?",
    level=2,
    theme="logs",
    signals=("logs",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("Lines written", "sending.lines_written"),
        Column("Unparsable", "sending.unparsable_written"),
        Column("Accepted", "counters.collector.logs.receiver_accepted"),
        Column("Refused", "counters.collector.logs.receiver_refused"),
        Column("Receiver failed", "measured.receiver_failed"),
        Column("Sent to the sink", "counters.collector.logs.exporter_sent"),
        Column("Arrived at the sink", "counters.sink.logs.receiver_accepted"),
        Column("Records printed", "measured.records_arrived"),
        Column("With a level attribute", "measured.records_with_level"),
        Column("Raw bodies", "measured.raw_bodies_arrived"),
        Column("Requests at the sink", "measured.requests_at_sink"),
        Column("Failed entry lines", "measured.failed_entry_lines"),
        Column("Emit failure lines", "measured.emit_failure_lines"),
        Column("Parser series", "measured.parser_series"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
    ),
    interpretation="""
No metric counts a parse failure. `otelcol_receiver_failed_log_records` reads 0
in all four variants, including the two that discarded ten records, and
`otelcol_receiver_refused_log_records` reads 0 as well. No published series name
contains `parser`, `operator`, `stanza` or `unparsed`. The author expected the
failed counter to move under `drop`; it does not, and that is the finding.

The documented behaviour of `on_error` held exactly. `send` and `send_quiet`
delivered 100 records, ten of them carrying the unparsable line as the body with
no `level` attribute and the Unix epoch instead of the timestamp parsed from the
file. `drop` and `drop_quiet` delivered 90 and no raw bodies. All twelve runs
agreed to the record, with no range in any cell. Leaving `on_error` unset
behaved as `send`, and the collector log says so in the field
`"action": "send"` of each failure.

A dropped entry is never counted as accepted. `otelcol_receiver_accepted_log_records`
reads 90 under `drop`, not 100, and it equals what the sink accepted in every
variant. The ten lines leave no trace in any counter: they are not accepted, not
refused, not failed, and not sent. From the Collector's metrics a file with ten
bad lines in a hundred looks exactly like a file with ninety good lines.

That is the operational problem. Alerting on the ratio needs a denominator the
Collector does not have, because nothing it publishes knows how many lines the
file held. The accepted rate falls by the drop rate, so a rate alert can catch a
sudden change in input shape, and only if the surrounding traffic is otherwise
steady.

The log text is the only direct surface, and `on_error` can remove it. `drop`
and the unset default each produced ten `Failed to process entry` lines at
`error`, naming the operator, the reason "regex pattern does not match" and the
action taken. The two `_quiet` variants produced none, which is the documented
behaviour: the failures are logged at `debug`, and the Collector's default log
level does not emit `debug`. A pipeline configured for `drop_quiet` discards a
tenth of its input with zero error lines, zero warn lines and no counter moving.

One line is worth knowing about. Each loud run also carries exactly one
`failed to emit token` line from the file consumer, and that line concatenates
all ten parse errors into a single `error` field. A count of error lines
therefore undercounts the failures by a factor that depends on how the reader
batched them, while this one line carries the true number in its text.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with one `file_log` receiver, one
`regex_parser` operator carrying a `timestamp` and a `severity` block, an OTLP
gRPC destination with the default queue and retry, and 100 lines written at
once. Only `on_error` varies.

The exact error-line count is not established. The Collector's default log
sampler allows 10 identical messages per tick before thinning, and this run
produced exactly 10 failures, so a true count and a capped count are the same
number here. A file with 30 bad lines would separate them, and that is the run
this question does not have.

All 100 lines were written in one `write` and the receiver read them in one
batch, which is why `requests_at_sink` is 1 everywhere. A file appended to over
minutes exercises `force_flush_period`, several reader batches and several
`failed to emit token` lines, none of which this run saw.

The failures were all of one kind: the regex did not match. An entry that
matches the regex but fails the `timestamp` or the `severity` block is a
different error path through the same `on_error` setting, and so is an oversized
line hitting `max_log_size`. Neither was tested.

The ratio was held at exactly one line in ten. Nothing here says whether the
Collector behaves differently when every line fails, which would make the
`send` variants a pipeline of raw bodies, or when the file is rotated or
truncated mid-read.

No storage extension was configured, so offsets lived in memory. A Collector
restarted mid-file is a different question, and the receiver's own
`retry_on_failure`, off by default, was never enabled.
""",
)
