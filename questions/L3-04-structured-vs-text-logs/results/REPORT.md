# L3-04 What do structured logs cost, and what do they save?

Level 3, theme logs. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L3-04`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `text-canonical` | every field in the body, parsed by the Collector |
| `text-drift-1pct` | the same text with 10 records in a second format |
| `structured` | a short body and typed attributes, nothing to parse |

## Measured

| Variant | Records sent | Request bytes | Request bytes per record | Batch item bytes | Batch bytes per record | Collector accepted | Sink accepted | Delivered | Unique sequences | Parsed records | Control count | Categorized | Unparsed | Request series | Miscounted | Warn lines | Error lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `text-canonical` | 1000 | 274381 | 274.4 | 179536 | 179.5 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 0 | 10 | 0 | 0 | 0 |
| `text-drift-1pct` | 1000 | 273931 | 273.9 | 178651 | 178.7 | 1000 | 1000 | 1000 | 1000 | 990 | 1000 | 990 | 10 | 11 | 10 | 0 | 0 |
| `structured` | 1000 | 440381 | 440.4 | 163676 | 163.7 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 0 | 10 | 0 | 0 | 0 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Structured records cost more on the wire and less inside the Collector. The
OTLP/HTTP JSON request for 1000 structured records was 440381 bytes, 440.4 per
record, against 274381 for the canonical text, 60.5% more, because every
attribute travels as a key and a typed value object. After parsing, the order
reversed: the batch processor counted 179536 item bytes for the canonical text,
which kept its full body and gained two attributes, against 163676 for
structured records, 9.7% more. Both differences failed the author's ranges,
the first by half a point above 60% and the second well under 20%.

The drift cost the metric, silently. With 10 of 1000 records in a second format,
the control count still read 1000, the route-by-outcome count put 990 records
in their 10 categories and the other 10 under one `unparsed` series, 11 series
in all, and the Collector logged 0 warn and 0 error lines. Its own counters
accepted and delivered all 1000. Only the `default_value` series shows the loss.

Canonical text and structured records counted identically: 1000 records, 10
series, 0 miscounted, every repetition the same to the byte.

What this does to a bill: structured logs cost about 60% more request bytes in
OTLP/JSON before compression, and save the parser's failure mode. A text format
that drifts keeps every record and loses its dimensions, so an alert on the
`unparsed` series is the only signal that a dashboard has started undercounting.

## What this does not say

Measured with OTLP/HTTP JSON, uncompressed, on `otelcol-contrib 0.162.0`, one
request of 1000 records, one regex and one drift pattern. Protobuf encoding and
gzip, which change the byte ratio, are L3-06's subject and were not run here.
Request bytes exclude HTTP headers and TCP; batch bytes are the processor's
item sizes, not a request on the wire.

No log backend was run, so nothing here is stored bytes, index size or a
vendor's bill. The `unparsed` count depends on the parser: a regex anchored
differently or a second pattern would catch other drift.
