# L0-01 What does the Collector do with a span you send by hand?

Level 0, theme traces. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L0-01`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `valid` | a resource, a 16 byte trace id and an end time |
| `short-trace-id` | the same span with a 15 byte trace id |
| `no-resource` | the same span with no resource at all |
| `no-end-time` | the same span with no end time |

## Measured

| Variant | HTTP status | Accepted | Refused | Spans printed | Resource attrs | Warn lines | End time is 1970 | Duration ms | Trace id hex chars | Response body |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `valid` | 200 | 1 | 0 | 1 | 1 | 0 | no | 120 | 32 | {"partialSuccess":{}} |
| `short-trace-id` | 400 | - | - | 0 | 0 | 0 | - | - | - | {"code":3,"message":"ID.UnmarshalJSONIter: length mismatch, error found in #1... |
| `no-resource` | 200 | 1 | 0 | 1 | 0 | 0 | no | 120 | 32 | {"partialSuccess":{}} |
| `no-end-time` | 200 | 1 | 0 | 1 | 1 | 0 | yes | - | 32 | {"partialSuccess":{}} |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Three of the four spans were accepted. Only one of them was accepted
unchanged.

The 15 byte `trace_id` never became a span. The receiver returned 400 with
`ID.UnmarshalJSONIter: length mismatch` before anything reached a pipeline,
and neither `otelcol_receiver_accepted_spans` nor
`otelcol_receiver_refused_spans` exists in that run, which is why both cells
read `-` rather than `0`. A Collector dashboard built on those counters is
blank, not red, while the sender loses every span it emits. The only place the
failure exists is the sender's own HTTP client.

The span with no resource was accepted and printed with zero resource
attributes. Nothing warned. A backend that groups by `service.name` has to put
that span somewhere, and the Collector has already agreed to forward it.

The span with no end time was accepted, and the Collector resolved the missing
field to the Unix epoch, printing `1970-01-01 00:00:00 +0000 UTC`. Over three
repetitions the collector log carried zero warnings for every variant. A
duration computed from that span is the age of the Unix epoch, which is the
kind of value that survives an average and destroys a percentile.

On a bill, none of this is expensive. On a dashboard, the 400 is the cheap
failure, because someone sees it immediately. The accepted-and-altered spans
are the expensive ones, because they arrive looking like data.

## What this does not say

Measured on `otelcol-contrib 0.162.0` over OTLP/HTTP with JSON encoding and
one span per request. The protobuf encoding and the gRPC transport have their
own decoders, and gRPC carries `trace_id` as raw bytes, where a length error
is a different kind of error. Nothing here says what happens to a batch in
which only one span is malformed, which is what a real SDK produces. The
`debug` exporter is a reading surface and not a backend: the zero end time is
the Collector's rendering of an absent field, and a vendor may store or
display it differently.
