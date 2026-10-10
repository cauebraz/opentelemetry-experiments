---
id: L4-04
title: What does it take to run a processor you wrote yourself?
level: 4
theme: collector
status: measured
---

## Question

The Collector Builder produces a binary with your own component in it. This
question measures that path: a processor that only stamps one attribute, the
binary the official distribution refuses, and what changes when the same logs
pass through the stock binary, the custom binary with no extra processor, and
the custom binary with the processor enabled.

## What the documentation says

> This program generates a custom OpenTelemetry Collector binary based on a
> given configuration.

Source: [`cmd/builder/README.md`](https://github.com/open-telemetry/opentelemetry-collector/blob/62cdad2ea133239380b44d20d84eb26e114779b6/cmd/builder/README.md?plain=1#L3),
`ocb v0.162.0`, commit `62cdad2ea133239380b44d20d84eb26e114779b6`. Local copy:
[`vendor/ocb-readme.md`](vendor/ocb-readme.md).

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/62cdad2ea133239380b44d20d84eb26e114779b6/cmd/builder/README.md | sed -n '3p'
```

## Hypothesis

One thousand logs, each with `sequence` and `keep=yes`, cross three
distributions. `stock-pass-through` is `otelcol-contrib 0.162.0`.
`custom-pass-through` and `custom-processor` are one OCB binary
(`ocb v0.162.0`, Go 1.26.0) that adds processor type `experiment`. Only
`custom-processor` enables it. The processor sets
`experiment.custom_processor=true` and leaves other attributes alone.

Predicted:

1. `otelcol-contrib validate` rejects a config whose processor type is
   `experiment`. The custom binary accepts that same config.
2. All three variants deliver 1000 logs to the sink.
3. Only `custom-processor` stamps the attribute, on every record, and every
   variant still shows `keep=yes`.
4. The custom image is a separate artifact: its byte size and build time are
   recorded once in `build-meta.json`, not inferred from the passthrough runs.

Invalid when delivery differs, when the stamp is missing or present on the
wrong variant, when an original attribute disappears, when the stock binary
accepts the custom type, when the custom binary rejects it, when a process
restarts or is OOM-killed, or when the binary size was not recorded. Three
repetitions, variants rotated.

## How to run

```bash
just ask L4-04
```

The runtime image is built from [`Dockerfile`](Dockerfile) (multi-stage, Go
1.26.0, `ocb v0.162.0`, scratch). Pins: Collector modules `v0.162.0` /
`v1.68.0` as required by that OCB release.

## What is measured

| Field | Meaning |
| --- | --- |
| delivered | log records in the sink debug log |
| stamped | records carrying `experiment.custom_processor=true` |
| kept | records that still carry `keep=yes` |
| startup_s | seconds from the first collector log line to "Everything is ready" |
| idle_rss_bytes | container memory before the burst |
| post_s / cpu_ns | cost of sending the 1000 logs |
| stock_reject_exit | `validate` of the custom config on otelcol-contrib |
| custom_accept_exit | `validate` of the same config on the custom image |
| binary_bytes / image_bytes / build_s | the one image build |

## Result

9 runs, 0 invalid. All three variants delivered 1000 logs and kept
`keep=yes` on every record. Only `custom-processor` stamped
`experiment.custom_processor=true` (1000 of 1000). Stock `validate` exited
1 on that config; the custom image exited 0. The custom binary is
31719586 bytes and the image build took 87.9 s. Idle RSS was about 44 MB
on otelcol-contrib and about 12 MB on the custom image. An earlier
session failed only the sink counter query and is kept in
[`results/invalid-prometheus-query/`](results/invalid-prometheus-query/).
See [`results/REPORT.md`](results/REPORT.md).

## What this does not say

Multi-arch images, an SBOM, and the time to ship a custom distribution
are outside the run. CPU on a 1000-log burst is noisy.
