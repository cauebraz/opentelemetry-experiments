---
id: L0-01
title: What does the Collector do with a span you send by hand?
level: 0
theme: traces
status: measured
---

## Question

A span is a JSON object you can type. Four versions of it go to the same OTLP
receiver: one correct, one with a `trace_id` that is 15 bytes instead of 16,
one with no `resource` at all, and one with no end time. The question is which
of the four the Collector refuses, which it accepts and changes, and which
surface tells you which happened.

The interesting part is not the correct span. It is the three that are wrong
in ways a real SDK or a real proxy can produce, and whether being wrong costs
you an HTTP error, a counter, a log line, or nothing at all.

## What the documentation says

On the identifiers, from the comment on `Span.trace_id`:

> The ID is a 16-byte array. An ID with all zeroes OR of length other than 16
> bytes is considered invalid (empty string in OTLP/JSON is zero-length and
> thus is also invalid).

Source: [`trace.proto`, lines 91-93](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.11.1/opentelemetry/proto/trace/v1/trace.proto#L91-L93),
`opentelemetry-proto v1.11.1`.

On the response, from the OTLP/HTTP section "Bad Data":

> If the processing of the request fails because the request contains data
> that cannot be decoded or is otherwise invalid and such failure is
> permanent, then the server MUST respond with `HTTP 400 Bad Request`.

Source: [`docs/specification.md`, line 655](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.11.1/docs/specification.md#L655),
`opentelemetry-proto v1.11.1`.

Both quotes checked against the raw files at the references above:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-proto/v1.11.1/opentelemetry/proto/trace/v1/trace.proto | grep -n 'of length other than 16 bytes'
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-proto/v1.11.1/docs/specification.md | grep -n 'such failure is permanent'
```

What the paragraphs leave open: the first says an invalid id is invalid, the
second says undecodable data gets a 400, and neither says whether a wrong
length is caught by the decoder or has to be noticed by the pipeline, which
`otelcol_*` counter moves in each case, or what the Collector fills in for
fields that are absent rather than wrong.

## Hypothesis

| Variant | What the author expected |
| --- | --- |
| `valid` | HTTP 200 with an empty JSON body, 1 accepted span, the span printed by the `debug` exporter with its resource attribute |
| `short-trace-id` | HTTP 400 with a body naming the field, 0 accepted and **0 refused**, because the failure happens while decoding the body, before the receiver hands anything to a pipeline, so `otelcol_receiver_refused_spans` does not even exist as a series |
| `no-resource` | HTTP 200, 1 accepted span, the `debug` exporter printing an empty resource section, and no warning anywhere |
| `no-end-time` | HTTP 200, 1 accepted span, the `debug` exporter printing a zero end time, and no warning anywhere |

The prediction the author cares about is `short-trace-id`: that a rejected
payload leaves no trace in the pipeline counters, so a dashboard built on
`otelcol_receiver_refused_spans` shows a healthy zero while the sender is
losing every span.

## How to run

```bash
just ask L0-01
```

Without the harness, start the stack and send one span by hand:

```bash
just up
curl -i -X POST http://127.0.0.1:24318/v1/traces \
  -H 'Content-Type: application/json' \
  -d '{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"l0-01"}}]},"scopeSpans":[{"scope":{"name":"opentelemetry_experiments"},"spans":[{"traceId":"5b8efff798038103d269b633813fc60c","spanId":"eee19b7ec3c1b174","name":"GET /checkout","kind":2,"startTimeUnixNano":"1760000000000000000","endTimeUnixNano":"1760000000120000000"}]}]}]}'
just logs collector
just down
```

Shorten `traceId` to 30 characters and send it again. That is the whole
experiment.

## What is measured

Held constant: one span per request, OTLP/HTTP JSON on port 4318, the shared
[`config/collector-debug.yaml`](../../config/collector-debug.yaml) with no
processors, so nothing between the receiver and the exporters can be blamed.
What varies is the payload.

| Field | Meaning |
| --- | --- |
| `sending.http_status` | the status the OTLP receiver returned |
| `sending.response_body` | the body it returned, truncated in the table |
| `counters.collector.traces.receiver_accepted` | `otelcol_receiver_accepted_spans` |
| `counters.collector.traces.receiver_refused` | `otelcol_receiver_refused_spans`, `-` when the series does not exist |
| `measured.debug_spans` | spans the `debug` exporter printed |
| `measured.resource_attributes` | resource attribute lines it printed |
| `measured.end_time_is_the_zero_instant` | whether the end time it printed is `1970-01-01 00:00:00 +0000 UTC` |
| `measured.duration_ms` | end minus start, as the Collector sees them |
| `measured.trace_id_hex_length` | hex characters in the trace id it printed |
| `log.warn_lines` | warnings in the collector log for the whole run |

No wall-clock value is recorded. The run date is not a result, so the
measurement keeps what the printed time means and throws the value away.

## Result

[`results/REPORT.md`](results/REPORT.md), 12 runs, none invalid.

The hypothesis held on all four variants. The 15 byte `trace_id` came back
400 with `ID.UnmarshalJSONIter: length mismatch`, and neither the accepted nor
the refused counter exists in that run, so both cells read `-` rather than
`0`: a Collector dashboard shows nothing at all while the sender loses every
span. The span with no resource and the span with no end time were both
accepted with zero warnings in the log, the second one with its end time
resolved to `1970-01-01 00:00:00 +0000 UTC`.

## What this does not say

Measured on `otelcol-contrib 0.162.0` over OTLP/HTTP with JSON encoding. The
protobuf encoding and the gRPC transport have their own decoders and may
answer differently, in particular because gRPC carries `trace_id` as raw bytes
where a length error is a different kind of error. One span per request says
nothing about a batch where only one span is malformed, which is the case a
real SDK produces and which L1 revisits. The `debug` exporter is a reading
surface and not a backend, so whatever the Collector prints for a field you
left out is the Collector's view of it, and a vendor may render the same span
differently.
