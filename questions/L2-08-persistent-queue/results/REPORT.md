# L2-08 What survives a Collector restart while the backend is down?

Level 2, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 3, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L2-08`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `memory-sigterm` | in-memory queue, Collector stopped with SIGTERM and started again |
| `memory-sigkill` | in-memory queue, Collector killed with SIGKILL and started again |
| `volume-sigterm` | file_storage queue on a volume, Collector stopped with SIGTERM |
| `volume-sigkill` | file_storage queue on a volume, Collector killed with SIGKILL |
| `volume-recreated` | file_storage queue on a volume, Collector killed with SIGKILL and recreated with a new volume |

## Measured

| Variant | HTTP status | Accepted before | Queue before | Stop seconds | Queue after restart | Delivered | Unique | Duplicates | Seconds to delivery | Drop log lines | Dropped items logged | Accepted after restart | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `memory-sigterm` | 200 | 100 | 10 | 0.53 (0.52 to 0.54) | 0 | 0 | 0 | 0 | - | 10 | 100 | - | 10 |
| `memory-sigkill` | 200 | 100 | 10 | 0.41 (0.37 to 0.47) | 0 | 0 | 0 | 0 | - | 0 | 0 | - | 0 |
| `volume-sigterm` | 200 | 100 | 10 | 0.65 (0.55 to 0.71) | 10 | 100 | 100 | 0 | 12.1 (11.6 to 12.1) | 10 | 100 | - | 10 |
| `volume-sigkill` | 200 | 100 | 10 | 0.47 (0.46 to 0.56) | 10 | 100 | 100 | 0 | 11.1 (10.1 to 11.6) | 0 | 0 | - | 0 |
| `volume-recreated` | 200 | 100 | 10 | 0.43 (0.42 to 0.44) | 0 | 0 | 0 | 0 | - | 0 | 0 | - | 0 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

In every variant the sender received HTTP 200 ten times, the Collector counted
100 accepted records and the queue held all 10 requests before the fault. What
happened next depended only on where the queue lived and on whether a new
volume came with the restart.

The memory queue lost all 100 records under both signals. Under SIGTERM the
shutdown wrote 10 `error` lines, `Exporting failed. Dropping data.` with
`dropped_items: 10` each and `interrupted due to shutdown` in the error field,
so the loss is in the log. Under SIGKILL the log has no error line at all, and
the restarted Collector published no `accepted`, `sent` or `send_failed` series
for logs: 100 acknowledged records left no trace in its telemetry.

The persistent queue on a volume delivered 100 of 100 under both signals, with
0 duplicates. The restarted Collector read 10 requests in
`otelcol_exporter_queue_size` before the sink came back, and its log carried
`Moved items for dispatching back to queue` with `numberOfItems: 10`. Delivery
finished 10.1 to 12.1 seconds after the sink started. The restarted process
reports 100 `sent` and no `accepted` series, so sent without accepted is what
redispatch from disk looks like in the self-metrics.

The SIGTERM shutdown of the persistent queue wrote the same 10 `Dropping data.`
error lines, totalling `dropped_items: 100`, as the memory queue, and then
delivered all 100. A log alert on that line fires on a loss that did not
happen, while the memory SIGKILL, the run that did lose 100 records, writes
nothing for it to match.

A new volume with the same persistent config behaved as the memory queue under
SIGKILL: queue 0 after the restart, 0 delivered, 0 error lines. The config is
not the evidence; the storage's lifetime is.

For a dashboard, `otelcol_exporter_queue_size` above zero just after a start
is the sign that a persistent queue recovered something. For an alert, neither
the restarted Collector's counters nor its log can report data lost with a
memory queue or a new volume; only the destination's received count, compared
with what senders were acknowledged, can.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with `file_storage` at its defaults
(`fsync: false`), 10 requests of 10 log records, an OTLP gRPC destination
stopped for the whole fault and `docker compose stop` with Docker's 10 second
grace. A shutdown that outlasts the grace, a host crash, which is the case
`fsync` exists for, and a full or slow disk were not tested.

The sink was down from before the first request until after the restart, so no
request was in flight to a live destination when the process ended. Duplicates
need a request the destination received but whose result the Collector never
recorded; this setup cannot produce one, and 0 duplicates here says nothing
about that window.

`volume-recreated` stands in for storage that does not outlive the container,
such as an `emptyDir` on a rescheduled pod; it was not run on Kubernetes. The
first session's `tmpfs-sigkill` runs measured a Docker mount rule for images
without `/tmp`, recorded in `QUESTION.md`, and are not in this table.
