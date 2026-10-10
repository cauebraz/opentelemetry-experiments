---
id: L1-03
title: Does a processor in one pipeline change what another pipeline sees?
level: 1
theme: collector
status: measured
---

## Question

One receiver feeding two pipelines is how the same telemetry goes to two
places with different processing: redacted to the vendor, complete to the
archive. That design is only safe if the two pipelines cannot see each other's
edits.

The configuration page says each pipeline gets its own instance of the
processor. It does not say whether each pipeline gets its own copy of the
data. Those are different claims, and the second is the one that decides
whether a redaction in one pipeline leaks into the other.

## What the documentation says

On sharing a component between pipelines:

> You can use the same receiver, processor, or exporter in more than one
> pipeline. When a processor is referenced in multiple pipelines, each pipeline
> gets a separate instance of the processor.

Source: [`configuration.md`, lines 632-634](https://github.com/open-telemetry/opentelemetry.io/blob/a12d86209bb807217742cc14e0a29095beaf978f/content/en/docs/collector/configuration.md?plain=1#L632-L634),
`opentelemetry.io` at commit `a12d86209bb8`, rendered at
[Configuration](https://opentelemetry.io/docs/collector/configuration/).

The answer is written down, but not on that page and not for users. It is a Go
comment on an internal package:

> It fans out the incoming data to all the consumers, and does smart routing:
> - Clones only to the consumer that needs to mutate the data.
> - If all consumers needs to mutate the data one will get the original
>   mutable data.

Source: [`internal/fanoutconsumer/traces.go`, lines 16-18](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.162.0/internal/fanoutconsumer/traces.go#L16-L18),
`opentelemetry-collector v0.162.0`.

Both quotes checked against the raw files at the references above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/a12d86209bb807217742cc14e0a29095beaf978f/content/en/docs/collector/configuration.md | grep -n 'separate instance of the processor'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/v0.162.0/internal/fanoutconsumer/traces.go | grep -n 'Clones only to the consumer'
```

What this leaves open, and it is the reason the question exists: the user-facing
page is silent, and the package that answers it is under `internal/`, so its
comment is not a contract. The second bullet is the unsettling one. When every
consumer mutates, one of them gets the original rather than a copy, so the
isolation depends on how many pipelines mutate and in which order they run.
Nothing tells a config author which pipeline is the one that gets the original,
or whether that matters.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `neither-mutates` | the span arrives at the sink with the attributes that were sent and no `pipeline` key, both pipelines report 1 sent |
| `one-mutates` | pipeline a prints `pipeline=a`, and the sink sees **no** `pipeline` key, because a mutating consumer gets the clone and the read-only consumer keeps the original |
| `both-mutate` | pipeline a prints `pipeline=a` and the sink sees exactly one value, `b`, with `Values at the sink` equal to 1 |

The prediction the author cares about is `both-mutate`. By the comment, one of
the two consumers receives the original mutable data, and if the Collector
handed the original to pipeline a and then let pipeline b write to the same
object, the sink could see `a`, or both `a` and `b`. The author expects
isolation to hold anyway, with `Values at the sink` equal to 1 in every
repetition, because the clone is made for all mutating consumers except the
last one. If that is right, two pipelines are a safe way to split processing.
If `Values at the sink` is ever 2, the pattern is unsafe and the configuration
page has to say so.

A second prediction, weaker: the span count is unaffected in every variant,
1 accepted and 2 counted as sent across the two pipelines, so nothing about
fan-out shows up as a delivery number.

## How to run

```bash
just ask L1-03
```

Three variants, three repetitions each. One span per run, carrying
`service.name=l1-03` and `http.route=/checkout`, posted over OTLP/HTTP JSON.

Pipeline a ends in the `debug` exporter, so what it holds is read from the
collector log. Pipeline b ends in the sink, whose `debug` exporter runs at
`detailed` for this question
([`config/sink-detailed.yaml`](../../config/sink-detailed.yaml)), so what arrived is
read from the sink log. Both logs are saved per run under
[`results/logs/`](results/logs).

Without the harness:

```bash
COLLECTOR_CONFIG=./questions/L1-03-env-vars-and-two-pipelines/config/collector-both-mutate.yaml \
  SINK_CONFIG=./config/sink-detailed.yaml just up
curl -i -X POST http://127.0.0.1:24318/v1/traces -H 'Content-Type: application/json' \
  -d '{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"l1-03"}}]},"scopeSpans":[{"scope":{"name":"opentelemetry_experiments"},"spans":[{"traceId":"5b8efff798038103d269b633813fc60c","spanId":"eee19b7ec3c1b174","name":"GET /checkout","kind":2,"startTimeUnixNano":"1760000000000000000","endTimeUnixNano":"1760000000120000000"}]}]}]}'
just logs collector | grep -A3 Attributes
just logs sink | grep -A3 Attributes
just down
```

## What is measured

Held constant: one span per run, one OTLP receiver, two trace pipelines,
pipeline a always ending in `debug` and pipeline b always ending in
`otlp_grpc/sink` with the queue and retry disabled. Only the processors change
between variants.

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the status the OTLP receiver returned |
| `counters.collector.traces.receiver_accepted` | spans the receiver ingested, once for the request and not once per pipeline |
| `counters.collector.traces.exporter_sent` | spans counted as sent across both exporters, so fan-out is visible as a number |
| `counters.sink.traces.receiver_accepted` | spans the second collector received |
| `measured.spans_printed_by_a` | `Span #n` blocks in the collector log |
| `measured.spans_printed_at_sink` | `Span #n` blocks in the sink log |
| `measured.pipeline_attr_in_a` | values of the `pipeline` attribute printed by pipeline a, or `absent` |
| `measured.pipeline_attr_at_sink` | the same, read from the sink |
| `measured.distinct_values_at_sink` | how many different values of `pipeline` reached the sink; anything above 1 is a leak |
| `log.warn_lines` | warnings in the collector log for the whole run |

## Result

9 runs, 0 invalid. The table is in [`results/REPORT.md`](results/REPORT.md).

| Variant | `pipeline=` in a | `pipeline=` at the sink | Distinct values at the sink |
| --- | --- | --- | --- |
| `neither-mutates` | absent | absent | 0 |
| `one-mutates` | `a` | absent | 0 |
| `both-mutate` | `a` | `b` | 1 |

The hypothesis held in every variant, including the one the author was unsure
about. Two pipelines on one receiver are isolated: pipeline a's insertion never
appears at the sink, and when both pipelines insert, each side sees only its
own value, in all three repetitions.

The second prediction held too. One span was accepted once
(`otelcol_receiver_accepted_spans` reads 1) and counted twice by
`otelcol_exporter_sent_spans`, once per pipeline.

What this does to a dashboard, and it is L0-03's finding again from a different
direction: `otelcol_exporter_sent_spans` counts copies, not telemetry. L0-03
measured a factor of three from three exporters on one pipeline; here the
factor is two from two pipelines on one receiver. The two causes multiply. A
panel that reads that counter as throughput reports accepted times the fan-out
of the configuration, and the number moves whenever a pipeline or an exporter
is added, with no change in traffic.

What it does to a design: an attribute inserted in one pipeline stayed out of
the other one, and the guarantee is not in the documentation an operator
reads. It is a comment on `internal/fanoutconsumer`, which is behaviour at
`v0.162.0` rather than a contract. A pipeline split that carries a compliance
requirement still needs a test using the actual redaction processor and fields;
this insertion experiment is not that test.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one span per run and the
`attributes` processor as the mutation. A processor that replaces the whole
resource, a connector joining the two pipelines, or a `batch` processor shared
between them under load are different code paths and are not covered. Both
pipelines here carry the same signal; a receiver feeding a traces pipeline and
a metrics pipeline never shares an object in the first place. Only the
`pipeline` attribute was compared, so a mutation the `debug` exporter does not
print would not have been seen, and one span per run says nothing about what
happens when a batch is shared.

The `${env:...}` half of this question's original title was dropped rather
than measured. The configuration page documents expansion and the
`${env:VAR:-default}` syntax with worked examples, which rule 2 of
[the method](../../docs/METHOD.md) puts out of scope. The
runner passes `Variant.options["environment"]` to
`stack.environment(extra=...)`; that hook stays in the harness for a question
that needs a variable the documentation leaves open.
