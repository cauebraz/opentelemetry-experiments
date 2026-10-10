---
id: L3-04
title: What do structured logs cost, and what do they save?
level: 3
theme: logs
status: measured
---

## Question

The same 1000 request-completion events, sent as a line of text that a
Collector parses, as the same text with 1% of lines in a second format, and as
typed attributes that need no parsing. This question measures the bytes each
shape costs on the OTLP/HTTP request and inside the Collector, and how many
records each lets a route-by-outcome metric count correctly.

## What the documentation says

On the body:

> a human-readable string message (including multi-line) describing the event in
> a free form or it can be a structured data composed of arrays and maps of other
> values.

Source: [`specification/logs/data-model.md`, lines 396-398](https://github.com/open-telemetry/opentelemetry-specification/blob/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/logs/data-model.md?plain=1#L396-L398),
OpenTelemetry specification `v1.61.0`, commit
`32c0b2c1d9e11f68da0e6e7b63044e57878590de`.

On counting by attribute:

> Optionally, include a `default_value` for an attribute, to count data that does not contain the attribute.

Source: [`connector/countconnector/README.md`, line 108](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/connector/countconnector/README.md?plain=1#L108),
`opentelemetry-collector-contrib v0.162.0`, commit
`ae8c507510f48f433ab47dd1c6b01a59d6c388b5`.

Both quotes checked against the raw files at those commits:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/32c0b2c1d9e11f68da0e6e7b63044e57878590de/specification/logs/data-model.md | grep -n 'a free form or it can be a structured data'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/connector/countconnector/README.md | grep -n 'to count data that does not contain the attribute'
```

The data model allows both shapes and says nothing about what either costs.
The connector says what happens to a record without the attribute, not how many
records lose it when a format drifts.

## Hypothesis

Written before the first run.

- The structured request is larger than the canonical text request, by 20% to
  60%, because OTLP/JSON spells every attribute as a key and a typed value
  object.
- After parsing, the order reverses inside the Collector: the canonical text
  record carries its body plus the two extracted attributes, so the batch
  processor's item bytes for `text-canonical` exceed `structured` by 20% to 50%.
- `text-canonical` and `structured` both count 1000 records in exactly 10
  `route x outcome` series, every count correct.
- `text-drift-1pct` counts the 10 drifted records under one `unparsed` series,
  11 series in all, with 990 records categorized and 10 miscounted. The control
  count without attributes reads 1000 in every variant, and nothing in the
  Collector's own counters or log says that 10 records failed to parse.

## How to run

```bash
just ask L3-04
```

One OTLP/HTTP JSON request carries 1000 log records with `service.name=l3-04`.
Each record has `event.sequence` set to its index. Records cycle through five
routes, 200 each, with 20 errors per route and 100 in all. Every record carries
a unique `request_id`, which is never a metric dimension.

| Variant | Body | Attributes sent |
| --- | --- | --- |
| `text-canonical` | `request completed route=... outcome=... duration_ms=... request_id=...` | `event.sequence` |
| `text-drift-1pct` | as above, except 10 records written as `<request_id> <outcome> took <n>ms on <route>` | `event.sequence` |
| `structured` | `request completed` | `event.sequence`, `http.route`, `outcome`, `duration_ms`, `request_id` |

The 10 drifted records are 9 successes and 1 error, the traffic's ratio. One
Collector config serves every variant: `transform/parse` extracts `http.route`
and `outcome` from the body only when `http.route` is absent, then `batch`, then
the sink and a `count` connector with a control count and a count by
`http.route` and `outcome` that defaults to `unparsed`. The connector's metrics
leave on the `prometheus` exporter.

## What is measured

| Field | Meaning |
| --- | --- |
| request bytes | the OTLP/HTTP JSON body, uncompressed, without HTTP headers |
| batch item bytes | `otelcol_processor_batch_batch_send_size_bytes_sum`, the records after parsing |
| delivered, unique, duplicates, unknown | sink records by `event.sequence` |
| parsed records | delivered records carrying both `http.route` and `outcome` |
| control count | `l3_04_records_total`, every record |
| categorized, unparsed | `l3_04_requests_total` with and without a parsed route |
| request series | series of `l3_04_requests_total` |
| miscounted | records whose `route x outcome` count differs from the traffic |

A run is invalid when the request is not answered 200, the Collector or sink
does not account for 1000 records, the sink log disagrees with the sink counter,
any sequence is missing, duplicated or unknown, the control count is not 1000,
or a byte reading is absent. A drifted record counted as `unparsed` is the
result, not a failure.

## Result

9 valid runs, every repetition identical; the full table is in
[`results/REPORT.md`](results/REPORT.md).

| Variant | Request bytes | Batch item bytes | Categorized | Unparsed | Series | Miscounted |
| --- | --- | --- | --- | --- | --- | --- |
| `text-canonical` | 274381 | 179536 | 1000 | 0 | 10 | 0 |
| `text-drift-1pct` | 273931 | 178651 | 990 | 10 | 11 | 10 |
| `structured` | 440381 | 163676 | 1000 | 0 | 10 | 0 |

Against the hypothesis:
- The structured request was 60.5% larger than the canonical text request,
  just outside the predicted 20% to 60%.
- Inside the Collector the order reversed as predicted, but canonical text
  exceeded structured by 9.7%, not the predicted 20% to 50%.
- The drift predictions held exactly: 11 series, 990 categorized, 10
  miscounted, a control count of 1000, and 0 warn or error lines in the
  Collector log.

The first full run had 2 invalid `structured` repetitions. The batch-bytes
metric was read before Prometheus had scraped it. The wait now also covers that
metric, and the second run recorded it in every repetition.

## What this does not say

OTLP/HTTP JSON only, uncompressed, one request, one regex and one drift
pattern. Protobuf and gzip change the byte ratio and belong to L3-06. Request
bytes exclude headers and TCP, and batch bytes are item sizes inside the
processor. No log backend was run, so nothing here is stored bytes or a bill.
