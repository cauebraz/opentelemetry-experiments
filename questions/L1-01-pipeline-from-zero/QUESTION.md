---
id: L1-01
title: What is the smallest Collector config that works, and what does it say when it is wrong?
level: 1
theme: collector
status: measured
---

## Question

A pipeline is a receiver, optional processors and an exporter. Written from
zero, how little can you leave in before `otelcol validate` refuses it, and how
useful is each refusal? The question is not whether a correct config works. It
is whether an incorrect one tells you which component, key or line to look at.

## What the documentation says

> If a component is configured, but not defined within the `service` section, then it's not enabled.

> The Collector requires one or more receivers.

Source: [`content/en/docs/collector/configuration.md`, lines 599 to 601 and 271](https://github.com/open-telemetry/opentelemetry.io/blob/8194a04075d8eb16e4ecdfd8bb6d0675d764fe18/content/en/docs/collector/configuration.md?plain=1#L599-L601),
the source of the [Collector configuration](https://opentelemetry.io/docs/collector/configuration/)
page, at `opentelemetry.io` commit `8194a04075d8eb16e4ecdfd8bb6d0675d764fe18`,
the default branch head on 2026-10-06. The website has no releases, so the
page is pinned to that commit and tested against `otelcol-contrib 0.162.0`.

Both sentences checked against the raw file at that commit:

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/8194a04075d8eb16e4ecdfd8bb6d0675d764fe18/content/en/docs/collector/configuration.md | grep -n "then it's not enabled"
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/8194a04075d8eb16e4ecdfd8bb6d0675d764fe18/content/en/docs/collector/configuration.md | grep -n 'The Collector requires one or more receivers'
```

The page says what is required and what is ignored. It does not say whether
`validate` reports an ignored component, or what an error message contains.

## Hypothesis

Written before the first run.

- `minimal` (one `otlp` receiver, one `debug` exporter, one logs pipeline)
  validates with exit code 0.
- `configured-unused` validates with exit code 0 and prints nothing about the
  unused exporter, because the page calls it "not enabled", not an error.
- `undefined-env` validates with exit code 0: an undefined `${env:...}`
  expands to an empty endpoint, and `validate` does not reject it.
- The other seven cases exit 1:
  - `no-receivers` and `no-exporters` name the `logs` pipeline;
  - `used-undeclared` and `unknown-type` name the offending component;
  - `misspelled-key` and `wrong-type` name the offending key;
  - `bad-indentation` is the only case whose message carries a line number.
- `unknown-type` does not suggest the correct type name.
- Each `validate` finishes in under 3 seconds, container start included.

## How to run

```bash
just ask L1-01
```

The question starts no stack. For each variant, `send` runs
`docker compose run --rm --no-deps collector validate` on one file and records
the exit code, stdout, stderr and elapsed time. The valid config is
[`config/minimal.yaml`](config/minimal.yaml). Each case in
[`config/cases/`](config/cases/) changes one thing in it, and that directory is
outside the `just validate` glob on purpose.

| Variant | The one change |
| --- | --- |
| `minimal` | none |
| `no-receivers` | the pipeline lists no receiver |
| `no-exporters` | the pipeline lists no exporter |
| `configured-unused` | an `otlp_grpc/unused` exporter is configured and in no pipeline |
| `used-undeclared` | the pipeline lists `otlp_grpc/missing`, which is not configured |
| `unknown-type` | an exporter of type `otlp_grcp` is configured and used |
| `misspelled-key` | `debug` has `verbosty: detailed` |
| `wrong-type` | `debug` has `sampling_initial: many` |
| `undefined-env` | the receiver's endpoint is `${env:L1_01_UNDEFINED}` |
| `bad-indentation` | the pipeline's `exporters` list and a `processors` key are mis-indented |

## What is measured

| Field | Meaning |
| --- | --- |
| exit code | what `otelcol validate` returned |
| names pipeline, component, key | whether the output contains the variant's expected token |
| line number | whether the output contains `line <n>` |
| suggestion | whether the output contains `did you mean` |
| output lines | non-empty lines of stdout plus stderr |
| seconds | wall time of the `docker compose run` |
| output digest | SHA-256 of the output with timestamps removed, to show it is deterministic |

A run is invalid when the container cannot run (exit code other than 0 or 1,
or a Docker daemon error) or when its output is empty for a non-zero exit.

## Result

30 valid runs; the full table is in [`results/REPORT.md`](results/REPORT.md).
Every repetition printed the same text once Compose's container progress lines
were removed, except `unknown-type`, covered below.

| Variant | Exit | What `validate` printed |
| --- | --- | --- |
| `minimal` | 0 | nothing |
| `configured-unused` | 0 | nothing |
| `undefined-env` | 0 | nothing |
| `no-receivers` | 1 | `service::pipelines::logs: must have at least one receiver` |
| `no-exporters` | 1 | `service::pipelines::logs: must have at least one exporter` |
| `used-undeclared` | 1 | `references exporter "otlp_grpc/missing" which is not configured` |
| `unknown-type` | 1 | `unknown type: "otlp_grcp" for id: "otlp_grcp/typo"`, then 57 valid values |
| `misspelled-key` | 1 | `'debugexporter.Config' has invalid keys: verbosty` |
| `wrong-type` | 1 | `'sampling_initial' expected type 'int', got unconvertible type 'string'` |
| `bad-indentation` | 1 | `yaml: line 10: did not find expected key` |

Every prediction held:
- The minimal config and the two silent cases exited 0.
- Each of the other seven exited 1 and named the pipeline, component or key
  predicted.
- Only the YAML error carried a line number.
- No message suggested a correction.
- Every `validate` took 0.25 to 0.31 seconds.

Two results were not predicted:
- The line number points at line 10, `pipelines:`, the start of the block
  that failed to parse. The mis-indented key is on line 16.
- `unknown-type` lists the same 57 valid exporter types in a different order
  on every run, so its output is never byte-identical. `otlp_grpc` is in the
  list, with no hint that it is one letter swap away.

## What this does not say

`validate` was run on files, not started: an empty endpoint that validates may
still fail at start, and that was not measured here. Ten cases on one logs
pipeline are not every error class. Processors, connectors, extensions,
`--set` flags and multiple `--config` sources were not run. The messages are
`otelcol-contrib 0.162.0`'s and may change in any release.
