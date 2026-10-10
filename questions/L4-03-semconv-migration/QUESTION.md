---
id: L4-03
title: How do you rename an attribute without losing the history behind it?
level: 4
theme: semconv
status: measured
---

## Question

`http.status_code` became `http.response.status_code`. Dashboards, alerts and
stored data refer to the old name. This question measures what the schema
processor can do, what it cannot, and what a dual-write period costs while both
names are live.

## What the documentation says

On the default hard rename:

> By default, the processor performs hard renames: when an attribute is
> migrated, the old name is removed and the new name is written. This can be
> disruptive during active migrations because queries, dashboards, and alerts
> referencing old attribute names silently stop matching.

On dual-write with `migration.from`:

> Adding a `migration` section to the config changes this behavior: when an
> attribute is renamed, both the old and new attribute names are written to the
> signal. This gives operators a window to update their queries, dashboards,
> and alerts before committing to the new names.
>
> If both the old and new attribute already exist on an incoming signal,
> neither is overwritten.

Source: [`processor/schemaprocessor/README.md`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/schemaprocessor/README.md?plain=1#L37-L41),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`. A local copy is at
[`vendor/schemaprocessor-README.md`](vendor/schemaprocessor-README.md).

The rename itself lands in schema `1.21.0`:

> `http.status_code: http.response.status_code`

Source: official schema file at semantic-conventions tag `v1.21.0`, commit
`e531541025992b68177a68b87628c5dc75c4f7d9`, vendored at
[`schemas/1.21.0.yaml`](schemas/1.21.0.yaml) with sha256 in
[`schemas/PROVENANCE.json`](schemas/PROVENANCE.json).

The pages say what the processor writes. They do not say how many historical
spans a query still finds, what dual-write does to attributes per span, or what
the processor reports when both names already arrive.

## Hypothesis

Each run first stores 100 historical spans under schema `1.20.0` with only
`http.status_code`, through a pass-through Collector, into the sink's
append-only log. The Collector is then recreated with one of three strategies
and 100 live spans reuse the same deterministic ids under schema `1.20.0`. Ten
of those live spans already carry both attribute names.

Predicted:

1. `none` (pass-through) never invents `http.response.status_code`. The 190
   spans that arrived with only the old name stay old-only; the ten that
   arrived with both names keep both. A new-only query matches none.
2. `hard-rename` rewrites live spans to only `http.response.status_code`,
   including the ten that already carried both names. Historical spans in the
   sink stay on the old name. Old-only matches the 100 historical spans;
   new-only matches the 100 live spans; `old OR new` matches all 200.
3. `dual-write` keeps both names on every live span. Spans that already had
   both names are not overwritten. `old OR new` matches all 200.
4. Output schema URL on translated live spans becomes the `1.21.0` target.
   `none` leaves `1.20.0`.
5. Hard-rename increases translated counts without writing the old name;
   dual-write increases attributes per live span by one for the renamed key.

History is the sink log, not a trace backend. A run is invalid when ids are
missing or duplicated unexpectedly, when an overwrite of an existing pair is
detected, when the output schema URL is wrong, when the Collector fetches a
schema from the public internet, when translation fails, or when a process
restarts or is OOM-killed. Three repetitions.

## How to run

```bash
just ask L4-03
```

Pins: `otelcol-contrib 0.162.0`, schemas from semantic-conventions `v1.21.0`
(`e5315410…`), served locally at `http://host.docker.internal:28080/` from the
rewritten copies under `schemas/served/` so the run needs no outbound network
and no public CA.

## What is measured

| Field | Meaning |
| --- | --- |
| historical / live delivered | spans printed in the sink after each phase |
| old-only / new-only / either | spans whose attributes match each query shape |
| attrs per live span | attribute count on live spans after the strategy |
| both-kept | live spans that arrived with both names and kept both |
| resource schema URL | SchemaURL the sink printed for live batches |
| translated / skipped / failures | `processor_schema_*` self-metrics |
| batch item bytes | `otelcol_processor_batch_batch_send_size_bytes_sum` |

## Result

Source: this question's [`results/results.json`](results/results.json). Nine valid runs.

| Variant | Old-only | New-only | Both | Old or new | Live attrs/span | Batch item B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `none` | 190 | 0 | 10 | 200 | 3.10 | 13734 |
| `hard-rename` | 100 | 100 | 0 | 200 | 3.00 | 14336 |
| `dual-write` | 100 | 0 | 100 | 200 | 4.00 | 16838 |

Historical spans stayed on the old name in every strategy. Hard rename made
live traffic invisible to an old-only query. Dual-write kept every live span
on both names and cost one attribute and about 17% more batch item bytes than
hard rename. Spans that already carried both names kept both under dual-write
and lost the old name under hard rename.

## What this does not say

History is the sink log, not a backend index. The schema processor's
`translated` self-metric was not observed under the scraped name in these
runs. Public fetches of `opentelemetry.io` schemas are untested.
