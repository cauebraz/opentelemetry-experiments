# L2-04 What happens to the 10% of log lines that do not match the parser?

Level 2, theme logs. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L2-04`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `default` | on_error left unset, which the documentation says is send |
| `send-quiet` | on_error send_quiet |
| `drop` | on_error drop |
| `drop-quiet` | on_error drop_quiet |

## Measured

| Variant | Lines written | Unparsable | Accepted | Refused | Receiver failed | Sent to the sink | Arrived at the sink | Records printed | With a level attribute | Raw bodies | Requests at the sink | Failed entry lines | Emit failure lines | Parser series | Warn lines | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `default` | 100 | 10 | 100 | 0 | 0 | 100 | 100 | 100 | 90 | 10 | 1 | 10 | 1 | 0 | 0 | 11 |
| `send-quiet` | 100 | 10 | 100 | 0 | 0 | 100 | 100 | 100 | 90 | 10 | 1 | 0 | 0 | 0 | 0 | 0 |
| `drop` | 100 | 10 | 90 | 0 | 0 | 90 | 90 | 90 | 90 | 0 | 1 | 10 | 1 | 0 | 0 | 11 |
| `drop-quiet` | 100 | 10 | 90 | 0 | 0 | 90 | 90 | 90 | 90 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

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
