# Contributing

This repository records OpenTelemetry experiments. Every measured question
keeps a hypothesis written before the first run, and every number in a result
comes from a self-metric, a query or a recorded response.

## Prerequisites

- Python 3.14, exact interpreter in `.python-version`
- Docker with a Unix socket (raw container stats require it)
- `uv` and `just` on `PATH`
- amd64 or arm64

`just ask` binds only to `127.0.0.1`. Existing results were recorded on Docker
Desktop for macOS; other hosts may differ in absolute CPU and network figures.

## Local checks

```bash
just setup
just check
```

`just check` runs format, lint, question lint, unit tests, Collector and
Prometheus config validation, and one smoke run through the real stack.

## Adding a question

```bash
just new-question L4-06 my-slug collector
```

Fill [`docs/METHOD.md`](docs/METHOD.md) and the new `QUESTION.md` top to bottom
up to and including "Hypothesis" before the first run. Then:

1. Add unit tests for workload, configs and validity rules.
2. Run `just ask <id>` (or with an explicit repetition count when rotation
   needs more than three).
3. Write "Result" and "What this does not say" from `results/REPORT.md`.
4. Mark the ladder row measured in `README.md`.
5. Add evidence-linked sentences to `LEARNINGS.md` only when they point at
   that question's `results.json`.

Do not edit a hypothesis after the first run. When a validity rule changes
after a session, record the amendment with a date and keep the invalid
artifacts.

## Pull requests

- Keep repository files in English, plain ASCII.
- Prefer `just` recipes over calling modules directly.
- Pin new dependencies and images by resolved version and digest or checksum.
- Do not commit secrets, local inputs under `config/input/` or
  `questions/**/input/`, or editor files.
