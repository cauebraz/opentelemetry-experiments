"""Emit the L3-03 workload with the real Python SDK and print one JSON line."""

import hashlib
import json
import sys
import time
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.trace import Status, StatusCode, set_span_in_context

from opentelemetry_experiments.core import stack

TRACES = 1000
CHILDREN = 4
ERROR_EVERY = 10
ERROR_CHILD = 2
QUEUE_SIZE = 8192


def derived(label: str, size: int) -> int:
    digest = hashlib.sha256(label.encode()).digest()
    return int.from_bytes(digest[:size], "big") or 1


def trace_id_for(index: int) -> int:
    return derived(f"l3-03/trace/{index}", 16)


def is_error(index: int) -> bool:
    return index % ERROR_EVERY == 0


class Sequential(IdGenerator):
    """The same trace and span ids in every run, in emission order."""

    def __init__(self) -> None:
        self.traces = 0
        self.spans = 0

    def generate_trace_id(self) -> int:
        value = trace_id_for(self.traces)
        self.traces += 1
        return value

    def generate_span_id(self) -> int:
        value = derived(f"l3-03/span/{self.spans}", 8)
        self.spans += 1
        return value


def sampler_for(name: str) -> Sampler:
    if name == "head-10":
        return ParentBased(TraceIdRatioBased(0.10))
    return ParentBased(ALWAYS_ON)


def hex_trace(value: int) -> str:
    return format(value, "032x")


def emit(name: str) -> dict[str, Any]:
    provider = TracerProvider(
        resource=Resource.create({"service.name": "l3-03"}),
        sampler=sampler_for(name),
        id_generator=Sequential(),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{stack.COLLECTOR_HTTP_URL}/v1/traces"),
            max_queue_size=QUEUE_SIZE,
        )
    )
    tracer = provider.get_tracer("l3-03")
    generated: list[str] = []
    errors: list[str] = []
    selected: list[str] = []
    exceptions: list[str] = []
    started = time.monotonic()
    for index in range(TRACES):
        try:
            root = tracer.start_span(f"request-{index:04d}")
            context = set_span_in_context(root)
            for child_index in range(CHILDREN):
                child = tracer.start_span(f"step-{child_index}", context=context)
                if is_error(index) and child_index == ERROR_CHILD:
                    child.set_status(Status(StatusCode.ERROR, "synthetic failure"))
                child.end()
            root.end()
        except Exception as error:
            exceptions.append(f"{type(error).__name__}: {error}")
            continue
        trace = hex_trace(root.get_span_context().trace_id)
        generated.append(trace)
        if is_error(index):
            errors.append(trace)
        if root.get_span_context().trace_flags.sampled:
            selected.append(trace)
    emitted = time.monotonic()
    flushed = provider.force_flush()
    try:
        provider.shutdown()
        shutdown = True
    except Exception as error:
        exceptions.append(f"{type(error).__name__}: {error}")
        shutdown = False
    return {
        "generated": generated,
        "errors": errors,
        "selected": selected,
        "spans_per_trace": CHILDREN + 1,
        "emit_seconds": round(emitted - started, 2),
        "flush_seconds": round(time.monotonic() - emitted, 2),
        "flushed": flushed,
        "shutdown": shutdown,
        "exceptions": exceptions,
    }


def main() -> int:
    options = json.loads(sys.argv[1])
    print(json.dumps(emit(options["scenario"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
