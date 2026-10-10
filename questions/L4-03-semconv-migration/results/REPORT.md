# L4-03 How do you rename an attribute without losing the history behind it?

Level 4, theme semconv. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L4-03`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `none` | pass-through Collector, no schema processor |
| `hard-rename` | schema target 1.21.0, hard rename |
| `dual-write` | schema target 1.21.0 with migration.from 1.20.0 |

## Measured

| Variant | Historical | Total delivered | Old-only | New-only | Old or new | Both names | Live attrs/span | Both kept | Translated | Batch item B | Schema URLs | Unique traces |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `none` | 100 | 200 | 190 | 0 | 200 | 10 | 3.10 | 10 | - | 13734 | ['http://host.docker.internal:28080/1.20.0'] | 100 |
| `hard-rename` | 100 | 200 | 100 | 100 | 200 | 0 | 3.00 | 0 | - | 14336 | ['http://host.docker.internal:28080/1.20.0', 'http://host.docker.internal:280... | 100 |
| `dual-write` | 100 | 200 | 100 | 0 | 200 | 100 | 4.00 | 100 | - | 16838 | ['http://host.docker.internal:28080/1.20.0', 'http://host.docker.internal:280... | 100 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The history stayed in the sink log. Every strategy left the 100 historical
spans on `http.status_code` alone. What changed was only the live batch.

Pass-through never invented the new name. Hard rename moved every live span
onto `http.response.status_code` only, including the ten that already carried
both names, so an old-only query lost the live half of the traffic. Dual-write
put both names on every live span and raised live attributes per span from
3.0 to 4.0; batch item bytes rose from 14336 to 16838 against 13734 with no
processor. An `old OR new` query matched all 200 spans in every strategy.

The practical sequence the numbers support is dual-write while queries move,
then hard rename once the old name is unused. Leaving migration on costs one
extra attribute per renamed key on every live span. History that already
landed under the old name is not rewritten by recreating the Collector; only
a backfill would change it.

## What this does not say

History is the sink's append-only debug log, not a trace store with its own
index. The `processor_schema_translated` self-metric was not present under
the scraped name in these runs, so translation counts come from the sink
attributes, not from that counter. Schemas were served locally from vendored
files; a Collector that must fetch `https://opentelemetry.io/schemas/...` on
the public internet is untested here.
