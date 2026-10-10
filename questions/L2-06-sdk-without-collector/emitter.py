"""Run one L2-06 scenario with the real Python SDK and print one JSON line."""

import json
import logging
import sys
import time
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from opentelemetry_experiments.core import stack
from opentelemetry_experiments.core.prometheus import wait_for_url

LONG_OUTAGE_SECONDS = 20.0
SETTLE_SECONDS = 10.0


class Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


class Scenario:
    def __init__(self, environment: dict[str, str], processor: str, queue: int | None):
        self.environment = environment
        exporter = OTLPSpanExporter(endpoint=f"{stack.COLLECTOR_HTTP_URL}/v1/traces")
        if processor == "simple":
            span_processor = SimpleSpanProcessor(exporter)
        elif queue is None:
            span_processor = BatchSpanProcessor(exporter)
        else:
            span_processor = BatchSpanProcessor(
                exporter, max_queue_size=queue, max_export_batch_size=queue
            )
        self.provider = TracerProvider(
            resource=Resource.create({"service.name": "l2-06"})
        )
        self.provider.add_span_processor(span_processor)
        self.tracer = self.provider.get_tracer("l2-06")
        self.spans: list[dict[str, Any]] = []
        self.exceptions: list[str] = []
        self.collector_ready_seconds: float | None = None

    def emit(self, count: int, phase: str) -> None:
        for _ in range(count):
            index = len(self.spans)
            span = self.tracer.start_span(f"span-{index:04d}")
            started = time.monotonic()
            try:
                span.end()
            except Exception as error:
                self.exceptions.append(f"{type(error).__name__}: {error}")
            self.spans.append(
                {
                    "index": index,
                    "phase": phase,
                    "trace_id": format(span.get_span_context().trace_id, "032x"),
                    "end_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )

    def stop_collector(self) -> None:
        stack.compose("stop", "collector", env=self.environment)

    def start_collector(self) -> None:
        started = time.monotonic()
        stack.compose("start", "collector", env=self.environment)
        wait_for_url(stack.COLLECTOR_HEALTH_URL)
        self.collector_ready_seconds = round(time.monotonic() - started, 2)

    def finish(self) -> bool:
        flushed = self.provider.force_flush()
        self.provider.shutdown()
        return flushed


def run(name: str, scenario: Scenario) -> None:
    if name == "collector-up":
        scenario.emit(100, "up")
    elif name == "simple-down":
        scenario.stop_collector()
        scenario.emit(3, "down")
        scenario.start_collector()
        scenario.emit(3, "up")
    elif name == "batch-short-outage":
        scenario.stop_collector()
        scenario.emit(100, "down")
        scenario.start_collector()
        time.sleep(SETTLE_SECONDS)
    elif name == "batch-long-outage":
        scenario.stop_collector()
        scenario.emit(50, "down")
        time.sleep(LONG_OUTAGE_SECONDS)
        scenario.start_collector()
        scenario.emit(50, "up")
    elif name == "small-queue":
        scenario.stop_collector()
        scenario.emit(200, "down")
        scenario.start_collector()
        time.sleep(SETTLE_SECONDS)
    else:
        raise ValueError(f"unknown scenario {name}")


def main() -> int:
    options = json.loads(sys.argv[1])
    recorder = Recorder()
    root = logging.getLogger("opentelemetry")
    root.addHandler(recorder)
    root.setLevel(logging.WARNING)
    scenario = Scenario(options["environment"], options["processor"], options["queue"])
    started = time.monotonic()
    run(options["scenario"], scenario)
    flushed = scenario.finish()
    print(
        json.dumps(
            {
                "spans": scenario.spans,
                "exceptions": scenario.exceptions,
                "collector_ready_seconds": scenario.collector_ready_seconds,
                "flushed": flushed,
                "seconds": round(time.monotonic() - started, 1),
                "sdk_log": recorder.records,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
