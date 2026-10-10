# L1-01 What is the smallest Collector config that works, and what does it say when it is wrong?

Level 1, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L1-01`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `minimal` | the smallest logs pipeline |
| `no-receivers` | the pipeline lists no receiver |
| `no-exporters` | the pipeline lists no exporter |
| `configured-unused` | an exporter configured and in no pipeline |
| `used-undeclared` | a pipeline exporter that is not configured |
| `unknown-type` | an exporter of type otlp_grcp |
| `misspelled-key` | debug has verbosty |
| `wrong-type` | debug has sampling_initial: many |
| `undefined-env` | the endpoint is an undefined env var |
| `bad-indentation` | the exporters list is mis-indented |

## Measured

| Variant | Exit code | Names token | Line number | Suggestion | Output lines | Seconds | Output digest |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `minimal` | 0 | - | False | False | 0 | 0.29 (0.25 to 0.29) | e3b0c44298fc |
| `no-receivers` | 1 | True | False | False | 1 | 0.27 (0.27 to 0.31) | 3afe60867a37 |
| `no-exporters` | 1 | True | False | False | 1 | 0.27 (0.27 to 0.28) | 2481048051ae |
| `configured-unused` | 0 | False | False | False | 0 | 0.28 (0.27 to 0.28) | e3b0c44298fc |
| `used-undeclared` | 1 | True | False | False | 1 | 0.28 (0.27 to 0.29) | 093c3721c32d |
| `unknown-type` | 1 | True | False | False | 2 | 0.27 (0.26 to 0.28) | varies: 17f4407d2790, 5eae20f01084, b8a7c2145956 |
| `misspelled-key` | 1 | True | False | False | 3 | 0.27 (0.27 to 0.28) | 348c4f9da7a7 |
| `wrong-type` | 1 | True | False | False | 3 | 0.27 (0.27 to 0.28) | 212fa5d00d6b |
| `undefined-env` | 0 | False | False | False | 0 | 0.28 (0.28 to 0.29) | e3b0c44298fc |
| `bad-indentation` | 1 | - | True | False | 1 | 0.27 | 3b6a0ac4ae75 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

`validate` is silent about what the documentation calls not enabled. An exporter
configured and listed in no pipeline, and a receiver whose endpoint is an
undefined environment variable, both exited 0 with no output, exactly like the
minimal config. Nothing on the command line separates a correct config from one
that drops a component or an endpoint.

The seven refusals each exited 1 and named the pipeline, component or key at
fault, in one to three lines. Only the YAML error carried a line number, and it
pointed at line 10, the start of the `pipelines` block, six lines above the
mis-indented key. The unknown type printed 57 valid values in a different
order on every run and no suggestion, with `otlp_grpc` among them.

What this does to an alert or a deploy: a CI step that runs `validate` catches
missing, undeclared, unknown and mistyped components, and does not catch a
component left out of `service` or an empty variable. Those need a check of
their own, such as diffing the configured components against the pipelines.

## What this does not say

Configs were validated, not started, so an empty endpoint that validates may
still fail at start. Ten cases on one logs pipeline; processors, connectors,
extensions, `--set` and multiple `--config` sources were not run. The messages
are `otelcol-contrib 0.162.0`'s.
