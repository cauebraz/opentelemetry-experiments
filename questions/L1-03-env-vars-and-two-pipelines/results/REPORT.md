# L1-03 Does a processor in one pipeline change what another pipeline sees?

Level 1, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L1-03`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `neither-mutates` | two pipelines, no processor in either |
| `one-mutates` | pipeline a inserts pipeline=a, pipeline b has no processor |
| `both-mutate` | pipeline a inserts pipeline=a, pipeline b inserts pipeline=b |

## Measured

| Variant | HTTP status | Accepted | Sent by both pipelines | Arrived at the sink | Spans printed by a | Spans printed at the sink | Values at the sink | Warn lines | pipeline= in a | pipeline= at the sink |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `neither-mutates` | 200 | 1 | 2 | 1 | 1 | 1 | 0 | 0 | absent | absent |
| `one-mutates` | 200 | 1 | 2 | 1 | 1 | 1 | 0 | 0 | a | absent |
| `both-mutate` | 200 | 1 | 2 | 1 | 1 | 1 | 1 | 0 | a | b |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The isolation holds. With one mutating pipeline, the attribute inserted in
pipeline a never reaches the sink. With both pipelines mutating, pipeline a
prints `pipeline=a`, the sink receives `pipeline=b`, and
`Values at the sink` is 1 in all three repetitions. Splitting one receiver into
two pipelines kept this inserted span attribute isolated, and the second
bullet of the fan-out comment, the one about a consumer getting the original
mutable data, did not turn into a crossover in these variants.

That is the answer, and it is worth noticing where the answer lives. The
configuration page says each pipeline gets its own instance of the processor,
which is a statement about components. The statement about data is a comment on
a package under `internal/`, so it is current behaviour rather than a promise,
and nothing in the user-facing documentation would stop a future release from
changing it. A design that depends on isolation between pipelines is depending
on an implementation detail, and the right response is a test in your own
repository, not a sentence in a runbook.

One number in the table is a trap, and it is the same trap L0-03 found one
layer down. A single span was accepted once and counted twice by
`otelcol_exporter_sent_spans`, because each pipeline's exporter counts its own
copy. In L0-03 the factor was three exporters on one pipeline; here it is two
pipelines on one receiver. Both multiply the same counter, and neither shows
up in `otelcol_receiver_accepted_spans`, which reads 1. The ratio between
accepted and sent is the fan-out of the configuration, so it changes whenever
someone adds a pipeline, and a dashboard that reads `sent` as volume moves with
it.

## What this does not say

Measured on `otelcol-contrib 0.162.0` with one span per run and the
`attributes` processor as the mutation. A processor that replaces the whole
resource, a connector between the pipelines, or a batch shared by two
pipelines under load are different code paths, and nothing here covers them.
The two pipelines carry the same signal; a receiver feeding a traces pipeline
and a metrics pipeline never shares an object to begin with. Only the
`pipeline` attribute was compared, so a mutation that changes something the
`debug` exporter does not print would not be seen.

The `${env:...}` half of this question's original title was dropped rather
than measured: the configuration page documents expansion and the
`${env:VAR:-default}` syntax with examples, which rule 2 of the method puts
out of scope.
