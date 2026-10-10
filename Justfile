set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

setup:
    uv sync --frozen

lock:
    uv lock

fix:
    uv run --frozen ruff check --fix opentelemetry_experiments questions tests smoke
    uv run --frozen ruff format opentelemetry_experiments questions tests smoke

format-check:
    uv run --frozen ruff format --check opentelemetry_experiments questions tests smoke

lint:
    uv run --frozen ruff check opentelemetry_experiments questions tests smoke

test:
    uv run --frozen pytest

build:
    docker compose build collector

validate: build
    docker compose config --quiet
    for config in ./config/collector-*.yaml ./config/sink*.yaml ./config/gateway*.yaml questions/*/config/*.yaml; do \
      [ -e "${config}" ] || continue; \
      case "${config}" in *prometheus*.yaml) continue ;; esac; \
      COLLECTOR_CONFIG="./${config#./}" docker compose run --rm --no-deps collector validate --config=/etc/otelcol-contrib/config.yaml || exit 1; \
    done
    for config in ./config/prometheus*.yaml questions/*/config/prometheus*.yaml; do \
      [ -e "${config}" ] || continue; \
      PROMETHEUS_CONFIG="./${config#./}" docker compose run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yaml || exit 1; \
    done

questions-lint:
    uv run --frozen python -m opentelemetry_experiments.lint_questions

reports-check:
    uv run --frozen python -m opentelemetry_experiments.render --check

# Start the real stack once and prove one item of each signal reaches the sink.
smoke: build
    uv run --frozen pytest smoke

check: format-check lint questions-lint reports-check test validate smoke

# Every question on the ladder, with its state.
ladder:
    uv run --frozen python -m opentelemetry_experiments.run --list

# Run one question, for example: just ask L0-01
ask question repetitions="3": build
    uv run --frozen python -m opentelemetry_experiments.run '{{question}}' --repetitions '{{repetitions}}'

# Re-render a question's REPORT.md from its recorded results.json.
render question:
    uv run --frozen python -m opentelemetry_experiments.render '{{question}}'

# Copy questions/_template into a new question directory.
new-question id slug theme:
    uv run --frozen python -m opentelemetry_experiments.new_question '{{id}}' '{{slug}}' --theme '{{theme}}'

up config="./config/collector-debug.yaml": build
    COLLECTOR_CONFIG='{{config}}' docker compose up --detach

down:
    docker compose down --volumes --remove-orphans

logs service="collector":
    docker compose logs --no-color '{{service}}'
