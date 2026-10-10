"""L1-03: one receiver, two pipelines, and whether a mutation crosses over."""

import re
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l1-03"}
SPAN_NAME = "GET /checkout"
ROUTE = {"http.route": "/checkout"}

CONFIG = "./questions/L1-03-env-vars-and-two-pipelines/config"
SINK = stack.SINK_DETAILED_CONFIG

PIPELINE_ATTRIBUTE = re.compile(
    r"^\s*->\s*pipeline:\s*Str\((?P<value>[^)]*)\)\s*$", re.M
)
SPAN_BLOCK = re.compile(r"^Span #\d+$", re.M)

VARIANTS = (
    Variant(
        name="neither-mutates",
        description="two pipelines, no processor in either",
        collector_config=f"{CONFIG}/collector-neither-mutates.yaml",
        sink_config=SINK,
    ),
    Variant(
        name="one-mutates",
        description="pipeline a inserts pipeline=a, pipeline b has no processor",
        collector_config=f"{CONFIG}/collector-one-mutates.yaml",
        sink_config=SINK,
    ),
    Variant(
        name="both-mutate",
        description="pipeline a inserts pipeline=a, pipeline b inserts pipeline=b",
        collector_config=f"{CONFIG}/collector-both-mutate.yaml",
        sink_config=SINK,
    ),
)


def send(variant: Variant) -> dict[str, Any]:
    payload = otlp.trace_payload(
        [otlp.span(SPAN_NAME, span_attributes=ROUTE)],
        SERVICE,
    )
    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "traces", payload)
    return result | {"spans_sent": 1}


def attribute_values(text: str) -> list[str]:
    return [match.group("value") for match in PIPELINE_ATTRIBUTE.finditer(text)]


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    """Pipeline a is read in the collector's debug output, b at the sink."""
    printed = stack.unprefixed(collector_log)
    arrived = stack.unprefixed(sink_log)
    in_a = attribute_values(printed)
    at_sink = attribute_values(arrived)
    return {
        "pipeline_attr_in_a": ", ".join(sorted(set(in_a))) or "absent",
        "pipeline_attr_at_sink": ", ".join(sorted(set(at_sink))) or "absent",
        "distinct_values_at_sink": len(set(at_sink)),
        "spans_printed_by_a": len(SPAN_BLOCK.findall(printed)),
        "spans_printed_at_sink": len(SPAN_BLOCK.findall(arrived)),
    }


QUESTION = Question(
    id="L1-03",
    title="Does a processor in one pipeline change what another pipeline sees?",
    level=1,
    theme="collector",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Accepted", "counters.collector.traces.receiver_accepted"),
        Column("Sent by both pipelines", "counters.collector.traces.exporter_sent"),
        Column("Arrived at the sink", "counters.sink.traces.receiver_accepted"),
        Column("Spans printed by a", "measured.spans_printed_by_a"),
        Column("Spans printed at the sink", "measured.spans_printed_at_sink"),
        Column("Values at the sink", "measured.distinct_values_at_sink"),
        Column("Warn lines", "log.warn_lines"),
        Column("pipeline= in a", "measured.pipeline_attr_in_a", kind="text"),
        Column("pipeline= at the sink", "measured.pipeline_attr_at_sink", kind="text"),
    ),
    interpretation="""
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
""",
    does_not_say="""
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
""",
)
