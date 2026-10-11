---
id: L2-08
title: What survives a Collector restart while the backend is down?
level: 2
theme: collector
status: measured
---

## Question

L2-01 and L2-07 showed that, with the default in-memory queue, a request the
receiver answered with HTTP 200 lives only in the Collector's memory until the
destination accepts it. Both kept the Collector process alive. A deploy, an
eviction or an OOM kill ends that process, and the question becomes what a
restart does to data that was acknowledged and not yet delivered.

The persistent queue is the documented answer. It moves the queue into a
storage extension, and `file_storage` writes it to a directory. Whether that
directory outlives the process is a property of where it is mounted, not of the
Collector config, and a config that says `storage: file_storage` reads the same
whether the directory is a volume or a tmpfs that disappears with the
container. This question restarts the Collector under a stopped sink with a
memory queue, with a persistent queue on a volume, and with a persistent queue
on `/tmp`, which this repository mounts as tmpfs.

## What the documentation says

> When persistent queue is enabled, the batches are being buffered using the
> provided storage extension - [filestorage] is a popular and safe choice. If
> the collector instance is killed while having some items in the persistent
> queue, on restart the items will be picked and the exporting is continued.

Source: [`exporter/exporterhelper/README.md`, line 98](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.162.0/exporter/exporterhelper/README.md?plain=1#L98),
`opentelemetry-collector v0.162.0`.

> `fsync` when set, will force the database to perform an fsync after each
> write. This helps to ensure database integrity if there is an interruption
> to the database process, but at the cost of performance.

Source: [`extension/storage/filestorage/README.md`, line 37](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.162.0/extension/storage/filestorage/README.md?plain=1#L37),
`opentelemetry-collector-contrib v0.162.0`.

Both quotes checked against the raw files at the references above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/v0.162.0/exporter/exporterhelper/README.md | grep -n 'on restart the items will be picked'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/extension/storage/filestorage/README.md | grep -n 'force the database to perform an fsync'
```

The page says "killed" without saying which signal, and the `fsync` paragraph
says an interruption can matter while `fsync` defaults to off. It says nothing
about the memory queue on a restart, nothing about duplicates when requests
that were in flight are picked again, and nothing about a storage directory
that does not survive the container. It also does not say what, if anything,
tells an operator that a memory queue was lost.

## Hypothesis

Every variant sends 10 OTLP/HTTP JSON requests of 10 log records to a
Collector whose sink is already stopped, waits until the exporter's queue
holds all 10 requests, ends the Collector, starts it again, and only then
starts the sink.

| Variant | What the author expected |
| --- | --- |
| `memory-sigterm` | 10 times HTTP 200, 100 accepted, queue size 10 before the stop; the stop finishes in under 2 seconds; the queue reads 0 after the restart; **0 of 100 delivered**; the collector log carries `Dropping data.` error lines from the shutdown naming the dropped items |
| `memory-sigkill` | the same 200s and the same queue of 10; **0 delivered**; no error line about the loss at all, because the process never runs a shutdown, and every counter of the restarted Collector reads zero, so nothing in its telemetry says that 100 acknowledged records were lost |
| `volume-sigterm` | the queue reads 10 after the restart, before the sink is back; **100 delivered, 0 duplicates**, within one retry interval (at most 30 seconds) of the sink starting |
| `volume-sigkill` | the same as `volume-sigterm`: 100 delivered and 0 duplicates, because `fsync: false` risks the page cache on a host crash and a killed process does not lose it |
| `tmpfs-sigkill` | the Collector starts again without an error, because `create_directory` recreates the directory; the queue reads 0 after the restart and **0 are delivered**, with no line about the loss, the same outcome as `memory-sigkill` under a config that names a persistent queue |

The prediction the author cares about is the pair `memory-sigkill` and
`tmpfs-sigkill`: an acknowledged loss of 100 records that no counter and no
log line of the restarted Collector records, in one case with a config that
says the queue is persistent.

## How to run

```bash
just ask L2-08
```

The first run builds `opentelemetry-experiments-l2-08:0.162.0` from this
question's `Dockerfile`, which is `Dockerfile.collector` plus a
`/var/lib/otelcol/file_storage` volume owned by the Collector's user. Every
variant runs on that image, so the config is the only thing that differs.

By hand, with that image running under `docker compose`:

```bash
docker compose stop sink
for i in $(seq 0 9); do curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:24318/v1/logs -H 'Content-Type: application/json' -d '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":"r'$i'"}}]}]}]}'; done
docker compose kill -s SIGKILL collector && docker compose start collector
docker compose start sink
```

## What is measured

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the status of all 10 requests when they agree, `mixed` otherwise |
| `sending.accepted_before_fault` | `otelcol_receiver_accepted_log_records` read from `:8888` before the fault |
| `sending.queue_before_fault` | `otelcol_exporter_queue_size` read from `:8888` before the fault |
| `sending.stop_seconds` | how long `docker compose stop` or `kill` took to return |
| `sending.queue_after_restart` | the highest `otelcol_exporter_queue_size` in the 5 seconds after the restarted Collector is healthy, with the sink still stopped |
| `sending.delivered` | `otelcol_receiver_accepted_log_records` on the sink once it stops moving |
| `sending.seconds_to_delivery` | seconds from the sink starting to its last change, empty when nothing arrived |
| `measured.unique_records` | distinct record bodies the sink printed at `verbosity: detailed` |
| `measured.duplicate_records` | records the sink printed more than once |
| `measured.drop_log_lines` | `Dropping data.` lines in the Collector log, across both processes |
| `measured.dropped_items` | the sum of `dropped_items` on those lines |
| `counters.collector.logs.receiver_accepted` | what the restarted Collector reports as accepted, scraped by Prometheus at the end |

Held constant: the image, 100 records in 10 requests, the stopped sink during
the fault and the restart, `timeout: 5s` and the default retry and queue
settings apart from `storage`. The variable is where the queue lives and how
the process ends: `docker compose stop` sends SIGTERM with Docker's 10 second
grace, `docker compose kill -s SIGKILL` gives none.

Amendment, 2026-10-11, after the first session and before the second: all
three `tmpfs-sigkill` runs were invalid, because the restarted Collector
exited with `failed to create extension "file_storage": mkdir /tmp/otelcol:
permission denied` and its health check never answered. The cause is Docker,
not the Collector. On Docker 29.8.2, a container whose image has no `/tmp`, as
this `FROM scratch` image, gets the tmpfs at mode `1777` on its first start and
at `755` after a restart of the same container; a probe image with the same
`FROM scratch` shape read `1777` and then `755` across one SIGKILL and start,
while the Alpine image, which ships `/tmp`, kept `1777`. The variant measured
that mount rule instead of a storage directory that does not survive, so it is
replaced by `volume-recreated`: the volume config, SIGKILL, then
`docker compose up --force-recreate --renew-anon-volumes collector`, which
starts a new container with a new, empty volume, as a pod rescheduled with an
`emptyDir`. Its collector log holds only the new process, so its validity rule
expects one start instead of two. The hypothesis above is unchanged. The first
session is kept in
[`results/session-1-results.json`](results/session-1-results.json), its logs
as `results/logs/session-1-*.log`, for example
[`session-1-tmpfs-sigkill-r1.log`](results/logs/session-1-tmpfs-sigkill-r1.log),
and the tmpfs config in
[`results/session-1-collector-queue-tmpfs.yaml`](results/session-1-collector-queue-tmpfs.yaml).

Prediction for `volume-recreated`, written with this amendment: the queue reads
0 after the restart, **0 of 100 are delivered**, no `Dropping data.` line, and
nothing in the new Collector's telemetry says the records existed.

## Result

15 valid runs in the second session; the full table is in
[`results/REPORT.md`](results/REPORT.md). Every variant answered 10 times
HTTP 200, counted 100 accepted and held 10 requests in the queue before the
fault.

| Variant | Queue after restart | Delivered | Duplicates | `Dropping data.` lines | Error lines |
| --- | --- | --- | --- | --- | --- |
| `memory-sigterm` | 0 | 0 | 0 | 10 (`dropped_items` 100) | 10 |
| `memory-sigkill` | 0 | 0 | 0 | 0 | 0 |
| `volume-sigterm` | 10 | 100 | 0 | 10 (`dropped_items` 100) | 10 |
| `volume-sigkill` | 10 | 100 | 0 | 0 | 0 |
| `volume-recreated` | 0 | 0 | 0 | 0 | 0 |

The deciding predictions held. With a memory queue, SIGKILL lost 100
acknowledged records and neither the log nor the restarted Collector's
self-metrics recorded it: no error line, and no `accepted`, `sent` or
`send_failed` series for logs after the restart. The persistent queue on a
volume delivered 100 of 100 with 0 duplicates under both signals, 10.1 to 12.1
seconds after the sink started, and the restarted Collector logged
`Moved items for dispatching back to queue` with `numberOfItems: 10`. The
`volume-recreated` prediction written with the amendment held: 0 delivered and
nothing logged. Stops took 0.37 to 0.71 seconds.

One result was not predicted. `volume-sigterm` wrote the same 10
`Exporting failed. Dropping data.` error lines as `memory-sigterm`, with
`interrupted due to shutdown` and `dropped_items` summing to 100, and then
delivered all 100. On a persistent queue that line does not mean the data is
gone.

One prediction failed, in the first session. `tmpfs-sigkill` was expected to
restart cleanly with an empty queue; the Collector did not start at all,
because of the Docker mount rule described in the amendment.

## What this does not say

Measured on `otelcol-contrib 0.162.0` on Docker Desktop for macOS, with
`file_storage` at `fsync: false`, 100 log records in 10 requests and the sink
stopped from before the first request until after the restart. A host crash,
a shutdown that outlasts Docker's 10 second grace and a full disk were not
tested, and because no request was in flight to a live destination when the
process ended, 0 duplicates here does not bound duplicates in production.
