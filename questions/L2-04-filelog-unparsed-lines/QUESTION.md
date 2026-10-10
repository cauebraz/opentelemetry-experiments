---
id: L2-04
title: What happens to the 10% of log lines that do not match the parser?
level: 2
theme: logs
status: measured
---

## Question

A log file where one line in ten fails the configured parser is the normal
case, not the exception. Are those lines dropped, passed through as raw bodies,
or do they stop the pipeline, and is there a counter that would let you alert on
the ratio.

The documentation describes the four `on_error` strategies in terms of the
operator and the entry. It does not say what the Collector publishes while that
is happening: whether `otelcol_receiver_failed_log_records` moves, whether a
dropped entry is still accepted by the receiver, or whether the ratio of bad
lines is visible anywhere except the log text.

## What the documentation says

On the strategies:

> The `on_error` parameter determines the error handling strategy an operator should use when it fails to
> process an entry. There are 4 supported values: `drop`, `drop_quiet`, `send` and `send_quiet`.
>
> Regardless of the method selected, all processing errors will be logged by the operator.

> ### `drop`
> In this mode, if an operator fails to process an entry, it will drop the entry altogether.
> This will stop the entry from being sent further down the pipeline.
>
> ### `drop_quiet`
> Same as `drop` with only difference that the failure will be logged in debug level. Useful, when best effort
> operators are defined which might flood the logs with errors.
>
> ### `send`
> In this mode, if an operator fails to process an entry, it will still send the entry down the pipeline.
> This may result in downstream operators receiving entries in an undesired format.
>
> ### `send_quiet`
> Same as `send` with only difference that the failure will be logged in debug level. Useful, when best effort
> operators are defined which might flood the logs with errors.

Source: [`pkg/stanza/docs/types/on_error.md`, lines 2-21](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/pkg/stanza/docs/types/on_error.md?plain=1#L2-L21),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`.

On the default:

> | `on_error`    | `send`           | The behavior of the operator if it encounters an error. See [on_error](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/pkg/stanza/docs/types/on_error.md). |

Source: [`pkg/stanza/docs/operators/regex_parser.md`, line 18](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/pkg/stanza/docs/operators/regex_parser.md?plain=1#L18),
same commit.

Both quotes checked against the raw files at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/pkg/stanza/docs/types/on_error.md | grep -n 'There are 4 supported values\|will be logged by the operator\|drop the entry altogether\|still send the entry'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/pkg/stanza/docs/operators/regex_parser.md | grep -n 'on_error'
```

Two sentences are testable and one is a claim about logging. "it will drop the
entry altogether" and "it will still send the entry down the pipeline" predict a
difference of exactly ten records at the destination. "Regardless of the method
selected, all processing errors will be logged by the operator" says the quiet
variants still log, at `debug`, which the Collector's default log level does not
emit.

None of it names a metric. Whether the Collector counts a parse failure
anywhere is what this question is for.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `default` | 100 accepted, 100 at the sink, 10 with the raw body and no `level` attribute, 10 error lines |
| `send-quiet` | the same 100 records, with 0 error lines because the failures are logged at `debug` |
| `drop` | 90 accepted and 90 at the sink, 0 raw bodies, 10 error lines |
| `drop-quiet` | the same 90 records, with 0 error lines |

The prediction that decides whether this is alertable is the counter. The
author expects `otelcol_receiver_refused_log_records` to stay at 0 in all four
variants, because the receiver is not refusing anything, and expects
`otelcol_receiver_failed_log_records` to move under `drop` and `drop_quiet`,
where ten entries were accepted from the file and never left the receiver. If it
stays at 0 there too, no metric distinguishes a pipeline that is quietly
discarding a tenth of its input from one that is healthy, and the ratio can
only be recovered from the log text, which the quiet variants hide.

The author expects the receiver's accepted counter to equal what arrives at the
sink in every variant, that is, that a dropped entry is never counted as
accepted.

The author also expects no `otelcol_*` series whose name contains `parser`,
`operator`, `stanza` or `unparsed`, so `measured.parser_series` should read 0
everywhere.

## How to run

```bash
just ask L2-04
```

Four variants, three repetitions each. Each run writes 100 lines into
`input/app.log` while the collector is already polling the directory. Ninety of
them match `^(?P<clock>\d{2}:\d{2}:\d{2}) (?P<level>[A-Z]+) (?P<message>.*)$`;
every tenth line is a truncated JSON fragment that cannot match. The file is
written beside its destination and moved into place, so the receiver never
reads a half-written file. The send waits for exactly 100 records under `send`
and exactly 90 under `drop`; a timeout invalidates the run. The file is removed
even when sending fails, so the next collector starts with nothing to tail.

Without the harness:

```bash
COLLECTOR_INPUT_DIR=./questions/L2-04-filelog-unparsed-lines/input \
  SINK_CONFIG=./config/sink-detailed.yaml \
  COLLECTOR_CONFIG=./questions/L2-04-filelog-unparsed-lines/config/collector-drop.yaml just up
printf '14:00:01 INFO request 001 served\n{"partial": "json", "n": 10\n' \
  > ./questions/L2-04-filelog-unparsed-lines/input/app.log
just logs collector | grep 'Failed to process entry'
curl -s http://127.0.0.1:28888/metrics | grep '^otelcol_receiver'
```

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.lines_written` | lines placed in the file, held at 100 |
| `sending.unparsable_written` | lines that cannot match the regex, held at 10 |
| `counters.collector.logs.receiver_accepted` | `otelcol_receiver_accepted_log_records` on the collector under test |
| `counters.collector.logs.receiver_refused` | `otelcol_receiver_refused_log_records` |
| `measured.receiver_failed` | `otelcol_receiver_failed_log_records` |
| `counters.collector.logs.exporter_sent` | records the exporter delivered |
| `counters.sink.logs.receiver_accepted` | records the sink accepted |
| `measured.records_arrived` | `LogRecord` blocks the sink's `debug` exporter printed at `detailed` |
| `measured.records_with_level` | those blocks carrying a `level` attribute, which only a parsed line has |
| `measured.raw_bodies_arrived` | those blocks whose body is one of the ten unparsable lines |
| `measured.requests_at_sink` | export requests the sink logged |
| `measured.failed_entry_lines` | `Failed to process entry` lines in the collector log |
| `measured.emit_failure_lines` | `failed to emit token` lines in the collector log |
| `measured.parser_series` | published `otelcol_*` names matching `parser`, `operator`, `stanza` or `unparsed` |
| `log.warn_lines`, `log.error_lines` | collector log entries counted by level |

Held constant: the same 100 lines in the same order, one `regex_parser`
operator with a `timestamp` and a `severity` block, an OTLP gRPC destination
with the default queue and retry, no batch processor, and
`otelcol-contrib 0.162.0`. Only `on_error` varies.

The receiver is configured as `file_log`. The `filelog` alias logs a
deprecation warning at `warn`, which would be counted as a pipeline warning in
this table.

The collector under test keeps the default log sampling, so
`failed_entry_lines` and `emit_failure_lines` are floors. The sink runs with
sampling off, so `records_arrived` is a count.

## Result

12 runs, 0 invalid. The table is in [`results/REPORT.md`](results/REPORT.md).
Every cell agreed across all three repetitions, so no cell carries a range.

| Variant | Accepted | Refused | Receiver failed | Arrived | With a level | Raw bodies | Failed entry lines | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `default` | 100 | 0 | 0 | 100 | 90 | 10 | 10 | 11 |
| `send-quiet` | 100 | 0 | 0 | 100 | 90 | 10 | 0 | 0 |
| `drop` | 90 | 0 | 0 | 90 | 90 | 0 | 10 | 11 |
| `drop-quiet` | 90 | 0 | 0 | 90 | 90 | 0 | 0 | 0 |

The documented behaviour held exactly. `send` and `send_quiet` delivered 100
records, ten of them carrying the unparsable line as the body with no `level`
attribute and the Unix epoch instead of the timestamp parsed from the file.
`drop` and `drop_quiet` delivered 90 and no raw bodies. Leaving `on_error`
unset behaved as `send`, and the log says so in the field `"action": "send"`
of each failure.

The counter prediction failed. `otelcol_receiver_failed_log_records` reads 0 in
all four variants, including the two that discarded ten records.
`otelcol_receiver_refused_log_records` reads 0 as predicted, no published series
name matches `parser`, `operator`, `stanza` or `unparsed`, and
`otelcol_receiver_accepted_log_records` reads 90 under `drop`, equal to what the
sink accepted. The accepted prediction held: a dropped entry is never counted as
accepted.

So the ten lines leave no trace in any counter. They are not accepted, not
refused, not failed and not sent. From the Collector's metrics, a file with ten
bad lines in a hundred is indistinguishable from a file with ninety good lines,
because nothing the Collector publishes knows how many lines the file held.

The log text is the only direct surface, and `on_error` can remove it. The loud
variants each produced ten `Failed to process entry` lines at `error`, naming
the operator and the reason "regex pattern does not match". The quiet variants
produced none, which is the documented behaviour: the failures go to `debug` and
the default log level does not emit it. A pipeline on `drop_quiet` discards a
tenth of its input with zero error lines, zero warn lines and no counter moving.

Each loud run also carries exactly one `failed to emit token` line from the file
consumer, and that line concatenates all ten parse errors into a single `error`
field. Counting error lines therefore undercounts failures by whatever the
reader batched, while this one line carries the true number in its text.

What this does to an alert: there is no ratio to alert on. The usable signals
are the accepted rate falling against its own baseline, and a count of
`Failed to process entry` which only exists while `on_error` is loud.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one `file_log` receiver, one
`regex_parser` operator carrying a `timestamp` and a `severity` block, an OTLP
gRPC destination with the default queue and retry, and 100 lines written at
once. Only `on_error` varies.

The exact error-line count is not established. The Collector's default log
sampler allows 10 identical messages per tick before thinning, and this run
produced exactly 10 failures, so a true count and a capped count are the same
number here. A file with 30 bad lines would separate them, and that is the run
this question does not have.

All 100 lines were written in one write and read in one batch, which is why
`requests_at_sink` is 1 everywhere. A file appended to over minutes exercises
`force_flush_period`, several reader batches and several `failed to emit token`
lines, none of which this run saw.

The failures were all of one kind: the regex did not match. An entry that
matches the regex but fails the `timestamp` or the `severity` block is a
different error path through the same `on_error` setting, and so is an oversized
line hitting `max_log_size`. Neither was tested.

No storage extension was configured, so offsets lived in memory. A Collector
restarted mid-file is a different question, and the receiver's own
`retry_on_failure`, off by default, was never enabled.
