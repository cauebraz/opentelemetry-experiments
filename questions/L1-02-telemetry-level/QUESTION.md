---
id: L1-02
title: Does the documented per-level metric list match what the Collector emits?
level: 1
theme: collector
status: measured
---

## Question

The documentation does not only describe the telemetry levels, it publishes the
list: 28 metric names at `basic`, 4 more at `normal`, 10 more at `detailed`.
That turns a vague question into a checkable one. For each level, does the
Collector publish the names the table says it publishes, does it publish names
the table does not mention, and are the names on the endpoint spelled the way
the table spells them.

The list matters because it is what a dashboard is written against. If a name
on the table never appears, the panel is empty and the alert never fires. L0-03
showed `level: none` publishes nothing at all; this question is about the three
levels where something is published.

## What the documentation says

On the levels, from the list of values for `level`:

> - `none`: no telemetry is collected.
> - `basic`: essential service telemetry.
> - `normal`: the default level, adds standard indicators on top of basic.
> - `detailed`: the most verbose level, includes dimensions and views.

On the lists themselves:

> The following tables group each internal metric by level of verbosity:
> `basic`, `normal`, and `detailed`. Each metric is identified by name and
> description and categorized by instrumentation type.

Source: [`internal-telemetry.md`, lines 139-142 and 431-435](https://github.com/open-telemetry/opentelemetry.io/blob/fb0a807365b1b7a90ed3315cd4c4e46e44a1b48e/content/en/docs/collector/internal-telemetry.md?plain=1#L139-L142),
`opentelemetry.io` at commit `fb0a807365b1`, rendered at
[Internal telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/#lists-of-internal-metrics).
The three tables this question checks are at lines 454-483 (`basic`), 487-492
(`normal`) and 507-518 (`detailed`), transcribed into `BASIC`, `NORMAL` and
`DETAILED` in this question's [`question.py`](question.py).

Both quotes checked against the raw file at the reference above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/fb0a807365b1b7a90ed3315cd4c4e46e44a1b48e/content/en/docs/collector/internal-telemetry.md | grep -n 'no telemetry is collected'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/fb0a807365b1b7a90ed3315cd4c4e46e44a1b48e/content/en/docs/collector/internal-telemetry.md | grep -n 'group each internal metric by level'
```

What the tables leave open, and the page half admits it: the note under the
`normal` table records that the batch processor metrics "were inadvertently
reverted to `basic` from v0.109.0 to v0.121.0", so the mapping between a name
and a level has drifted before. The tables also list every name
unconditionally, while most of them belong to a component: nothing says which
ones a Collector without a scraper, without a queue or without a processor is
expected to publish. And the page's own sections on the `_total` suffix and on
dots against underscores say the published name can differ from the listed
name, without saying by how much at the endpoint this repository scrapes.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `level-none` | nothing published, as L0-03 measured; 0 of 0 documented names missing |
| `level-basic` | around 20 of the 28 published, with the 8 absentees being the component-conditional families: both `otelcol_scraper_*` (no scraper in the config) and the three `otelcol_exporter_enqueue_failed_*` (nothing failed to enqueue); the four batch metrics absent, which is what the note says `basic` should do |
| `level-normal` | the same `basic` set plus **all four** batch metrics, because the pipeline has a `batch` processor and this is the level the note says they belong to |
| `level-detailed` | the `normal` set plus the `http.server.*` and `rpc.client.*` families, because we post over OTLP/HTTP and export over gRPC; `rpc.server.*` and `http.client.*` absent, because no gRPC request arrives and no HTTP export leaves; `otelcol_exporter_enqueue_size` absent with the queue disabled |

Two predictions the author cares about, because both would change how a
dashboard is built:

- Some documented names never appear at any level, not because the level is
  wrong but because the component is not there. The author expects at least
  five such names at `detailed`, which means the table cannot be read as a
  checklist of panels to create.
- At least one name is published with a different spelling than the table
  gives, because the pull reader in this repository runs `without_units: true`.
  The author expects the unit-carrying names (`otelcol_process_cpu_seconds`,
  the `*_bytes` gauges) to be the ones that differ, and the
  `Matched without unit` column to be greater than zero at every level that
  publishes anything.

A third, weaker prediction: no undocumented `otelcol_*` name appears at
`basic` or `normal`, and any appearing at `detailed` is an `http.*` or `rpc.*`
family the table already lists in some form.

## How to run

```bash
just ask L1-02
```

Four variants, one per level, three repetitions each. Each run sends one batch
per signal, so every documented signal family has a chance to exist: 100 metric
datapoints, one span, one log record.

Without the harness, start the stack at one level and read the names:

```bash
COLLECTOR_CONFIG=./questions/L1-02-telemetry-level/config/collector-level-normal.yaml just up
curl -s 'http://127.0.0.1:29090/api/v1/series?match[]={job="collector"}' | jq -r '.data[].__name__' | sort -u
just down
```

## What is measured

Held constant: the same pipeline in all four variants, an OTLP receiver
feeding a `batch` processor and three exporters (`debug`, `otlp_grpc/sink`,
`prometheus`), differing only in `service.telemetry.metrics.level`. The
`batch` processor is there on purpose: without a processor the four
`normal`-level names could not appear at any level, and the comparison between
`basic` and `normal` would be empty.

Names are compared as families, not as series: dots become underscores, and a
trailing `_total`, `_bucket`, `_sum` or `_count` is removed, because those are
added by the Prometheus reader rather than by the Collector. A documented name
also counts as present when it appears without its unit suffix (`_seconds`,
`_bytes`), since the reader is configured `without_units: true`; the
`Matched without unit` column counts how often that happened.

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the worst status of the three posts |
| `measured.metric_families_published` | `otelcol_*`, `http_*` and `rpc_*` families the `collector` job published |
| `measured.published_names` | the complete normalized family list, truncated only in `REPORT.md` |
| `measured.documented_for_level` | names the tables list for this level and below |
| `measured.documented_present` | of those, how many were published |
| `measured.documented_missing` | of those, how many were not |
| `measured.missing_names` | which ones, complete in `results.json` and truncated in the table |
| `measured.undocumented_present` | published families no table lists at any level |
| `measured.batch_metrics_present` | how many of the four `normal`-level batch names exist |
| `measured.unit_suffix_dropped` | documented names that matched only without their unit suffix |
| `counters.collector.metrics.receiver_accepted` | that the burst was really ingested, so an empty list is not an empty run |
| `log.warn_lines` | warnings in the collector log for the whole run |

The published names are read from Prometheus with
`/api/v1/series?match[]={job="collector"}`, which is per scrape job, so the
sink's identical names cannot be mistaken for the collector's.

## Result

12 runs, 0 invalid. The table is in
[`results/REPORT.md`](results/REPORT.md), the complete name lists in
[`results/results.json`](results/results.json).

| Level | Metric families published | Documented at level | Present | Missing | Undocumented |
| --- | --- | --- | --- | --- | --- |
| `none` | 0 | 0 | 0 | 0 | 0 |
| `basic` | 19 | 28 | 16 | 12 | 3 |
| `normal` | 23 | 32 | 20 | 12 | 3 |
| `detailed` | 28 | 42 | 25 | 17 | 3 |

The hypothesis held in three places and failed in two.

Held: `normal` adds exactly the four `otelcol_processor_batch_*` names the
table says it adds. `detailed` adds the three `http.server.*` families and
`rpc.client.call.duration` and leaves out `http.client.*` and
`rpc.server.call.duration`, which is the shape of the traffic rather than the
level. More than five documented names are absent because the component is
not configured, so the table cannot be read as a list of panels to create.

Failed: the author predicted the pull reader's `without_units: true` would
shorten the unit-carrying names, and `Matched without unit` is 0 at every
level, so the names arrive spelled exactly as the table spells them. The
author also predicted no undocumented `otelcol_*` name below `detailed`, and
`otelcol_receiver_failed_spans`, `_metric_points` and `_log_records` appear
already at `basic`. They are declared in `receiverhelper` at this version and
are in none of the three tables:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/v0.162.0/receiver/receiverhelper/internal/metadata/generated_telemetry.go | grep -n 'otelcol_receiver_failed'
```

The prediction about which 12 names would be missing at `basic` was also
wrong in its reasoning. The author named the `otelcol_scraper_*` pair and the
three `otelcol_exporter_enqueue_failed_*`; the measurement adds the three
`otelcol_exporter_send_failed_*`, the two queue gauges and the two
`otelcol_processor_*_items`. The queue names follow from `sending_queue` being
disabled. The `processor` pair is the surprise: the pipeline has a `batch`
processor, and those two metrics are declared in `processorhelper`, which
`batchprocessor` does not use for its telemetry.

What this does to a dashboard: `otelcol_exporter_send_failed_*` is listed at
`basic` and does not exist until something fails, while
`otelcol_receiver_refused_*` is listed at the same level and reads zero from
the first request. A panel on the first shows "no data" on a healthy
collector, which is indistinguishable from a panel pointed at a metric that
was never registered. Alert on `rate()` of the receiver counters, which exist
from the start, and treat the exporter failure counters as something that
appears when the trouble does.

## What this does not say

Measured on `otelcol-contrib 0.162.0` against the page at commit
`fb0a807365b1`. Both move independently, so the comparison is a snapshot of
one pair and not a property of either. The absences belong to this
configuration as much as to the level: enabling `sending_queue`, adding a
scraping receiver, or using a processor built on `processorhelper` would
publish names that are missing here.

Nothing here is about cost. The question counts metric names, and `detailed`
is described as including "dimensions and views", which is a statement about
label cardinality: the same name can carry many more series at a higher level,
and that is the expense the page warns about. L3-01 is where cardinality is
measured. Histogram families are counted once each, so the `_bucket`, `_sum`
and `_count` series they expand into are not in `Metric families published`.
