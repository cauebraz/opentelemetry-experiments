# OpenTelemetry experiments

Recorded experiments about OpenTelemetry, one question at a time. Every
question states what it asks, which paragraph of the official documentation it
tests, a hypothesis written before the first run, and the numbers that came
back. The ladder goes from a span sent by hand with `curl` to a decision
defended with a `results.json`.

This repository does not restate the documentation and does not replace it. It
measures what the documentation leaves to the reader, and it shows the working.

## How to use it

Pick a question, read only its "Question" and "What the documentation says"
sections, write down what you expect, then read the author's hypothesis and
run. The disagreements between the two and the table are the lesson.

```bash
git clone https://github.com/cauebraz/opentelemetry-experiments
cd opentelemetry-experiments
just setup                 # uv sync, pinned
just ladder                # every question, run here, external or not yet
just ask L0-01             # run one question, three repetitions
just render L0-01          # re-render its REPORT.md from the recorded JSON
just smoke                 # one span, metric and log through the real stack
just check                 # ruff, pytest, otelcol validate, promtool, smoke
```

`just ask` needs Docker, `uv` and `just` on PATH. Nothing listens outside
`127.0.0.1`.

Requirements that matter for reproduction:

- Python 3.14, exact interpreter in `.python-version`
- Bash (the `Justfile` sets `shell := ["bash", ...]`)
- Docker on amd64 or arm64; raw CPU and RX counters need a Unix socket
- Recorded results came from Docker Desktop on macOS; absolute CPU and
  network numbers can differ on other hosts

This tree has twenty-five measured questions, levels 0 to 4. Each Level 4
question keeps its hypothesis next to the recorded `results.json`.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## The ladder

Each level names what you can do afterwards, not what you know. Themes
(Collector, traces, metrics, logs, SDK, semconv) cut across levels.

### Level 0, the wire

Afterwards: you read an OTLP payload and know what every field is.

| Question | Theme | Status |
| --- | --- | --- |
| [L0-01 A span on the wire](questions/L0-01-span-on-the-wire/QUESTION.md) | traces | measured |
| [L0-02 A metric and a log on the wire](questions/L0-02-metric-and-log-on-the-wire/QUESTION.md) | metrics | measured |
| [L0-03 Where the Collector tells you what it did](questions/L0-03-where-the-collector-tells-you/QUESTION.md) | collector | measured |

### Level 1, the Collector

Afterwards: you write a config from zero and know where to look when nothing
arrives.

| Question | Theme | Status |
| --- | --- | --- |
| [L1-01 A pipeline from zero](questions/L1-01-pipeline-from-zero/QUESTION.md) | collector | measured |
| [L1-02 The documented per-level metric list against what is emitted](questions/L1-02-telemetry-level/QUESTION.md) | collector | measured |
| [L1-03 Two pipelines on one receiver](questions/L1-03-env-vars-and-two-pipelines/QUESTION.md) | collector | measured |
| [L1-04 gRPC against HTTP on the receiver](questions/L1-04-grpc-vs-http-receiver/QUESTION.md) | collector | measured |

### Level 2, what breaks

Afterwards: you say how much data each failure loses and which counter shows
it.

| Question | Theme | Status |
| --- | --- | --- |
| [L2-01 The backend is down and the queue is on](questions/L2-01-backend-down/QUESTION.md) | collector | measured |
| [L2-02 Where memory_limiter sits](questions/L2-02-memory-limiter-placement/QUESTION.md) | collector | measured |
| [L2-03 How batch decides to send](questions/L2-03-batch-sizing/QUESTION.md) | collector | measured |
| [L2-04 Lines filelog cannot parse](questions/L2-04-filelog-unparsed-lines/QUESTION.md) | logs | measured |
| [L2-05 Traces while the exporter fails](questions/L2-05-traces-under-exporter-failure/QUESTION.md) | traces | measured |
| [L2-06 The SDK with no Collector to talk to](questions/L2-06-sdk-without-collector/QUESTION.md) | sdk | measured |
| [L2-07 When the exporter stops retrying](questions/L2-07-retry-exhausted/QUESTION.md) | collector | measured |

### Level 3, what it costs

Afterwards: you estimate the bill of a decision before taking it.

| Question | Theme | Status |
| --- | --- | --- |
| [L3-01 What cardinality costs](questions/L3-01-cardinality/QUESTION.md) | metrics | measured |
| [L3-02 What reduces histogram cardinality](questions/L3-02-histogram-reduction/QUESTION.md) | metrics | measured |
| [L3-03 Head against tail sampling](questions/L3-03-head-vs-tail-sampling/QUESTION.md) | traces | measured |
| [L3-04 Structured against text logs](questions/L3-04-structured-vs-text-logs/QUESTION.md) | logs | measured |
| [L3-05 What the spanmetrics connector costs](questions/L3-05-spanmetrics-connector/QUESTION.md) | traces, metrics | measured |
| [L3-06 Compression and protocol on the exporter](questions/L3-06-compression-and-protocol/QUESTION.md) | collector | measured |

### Level 4, what you decide

Afterwards: you write the decision with alternatives, cost and risk, with a
`results.json` behind it. L4-01 through L4-05 are measured.

| Question | Theme | Status |
| --- | --- | --- |
| [L4-01 Agent against gateway](questions/L4-01-agent-vs-gateway/QUESTION.md) | collector | measured |
| [L4-02 A view in the SDK against a transform in the platform](questions/L4-02-view-vs-transform/QUESTION.md) | sdk, metrics | measured |
| [L4-03 Migrating a semantic convention](questions/L4-03-semconv-migration/QUESTION.md) | semconv | measured |
| [L4-04 A processor you wrote yourself](questions/L4-04-custom-processor-with-ocb/QUESTION.md) | collector | measured |
| [L4-05 Remote write against scrape](questions/L4-05-remote-write-vs-scrape/QUESTION.md) | metrics | measured |

L3-01 and L3-02 link to the published
[cardinality lab](https://github.com/cauebraz/opentelemetry-cardinality-lab)
instead of duplicating it.

## The method in five lines

- A hypothesis is written before the first run, and it stays in the file
  afterwards, including when it was wrong.
- Every question names the documentation paragraph it tests, with the URL and
  the sentence quoted.
- Three repetitions. Every number comes from a self-metric, a query or a
  recorded response.
- Each result says what it does to a dashboard, an alert or a bill, and what it
  does not say.
- [`LEARNINGS.md`](LEARNINGS.md) accepts only sentences that link the
  `results.json` behind them.

[`docs/METHOD.md`](docs/METHOD.md) has the long version, the stack, the
dependent variables and the pins.

## Layout

```
|-- Justfile                  setup, check, smoke, ladder, ask, render, new-question
|-- compose.yaml              collector under test, optional gateway, sink, Prometheus
|-- Dockerfile.collector      otelcol-contrib, checksum verified
|-- config/                   collector-debug.yaml, sink.yaml, sink-detailed.yaml,
|                             gateway.yaml, prometheus.yaml, input/ mounted at /input
|-- docs/METHOD.md            the rules, the stack, the pins
|-- LEARNINGS.md              measured sentences by theme
|-- opentelemetry_experiments/
|   |-- core/                 otlp, prometheus, stack, runner, report, debuglog
|   |-- run.py                just ask: run a question, write results and report
|   |-- render.py             just render: REPORT.md from a recorded results.json
|   |-- lint_questions.py     just questions-lint: front matter and sections
|   `-- new_question.py       just new-question: copy and fill the template
|-- questions/
|   |-- _template/            what `just new-question` copies
|   `-- L0-01-span-on-the-wire/
|       |-- QUESTION.md       question, hypothesis, result
|       |-- question.py       variants, send, measure, optional validate_run
|       |-- config/           the collector config this question needs
|       |-- input/            files a file-reading receiver tails, untracked
|       `-- results/          results.json, REPORT.md, logs/ (collector and sink)
|-- smoke/                    just smoke: the real stack, one item per signal
`-- tests/                    unit tests, no Docker
```

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
