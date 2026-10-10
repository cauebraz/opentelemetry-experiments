"""Emit the L3-05 workload with the real Python SDK and print one JSON line."""

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
from opentelemetry.trace import SpanKind, Status, StatusCode

from opentelemetry_experiments.core import stack

TRACES = 1000
ROUTES = ("/checkout", "/cart", "/search", "/login", "/profile")
SUCCESS_MS = 100
ERROR_MS = 1000
QUEUE_SIZE = 8192
MS = 1_000_000


def derived(label: str, size: int) -> int:
    digest = hashlib.sha256(label.encode()).digest()
    return int.from_bytes(digest[:size], "big") or 1


def route_of(index: int) -> str:
    return ROUTES[index % len(ROUTES)]


def is_error(index: int) -> bool:
    return (index // len(ROUTES)) % 10 == 0


def customer_of(index: int) -> str:
    return f"customer-{derived(f'l3-05/customer/{index}', 6):012x}"


class Sequential(IdGenerator):
    """The same trace and span ids in every run, in emission order."""

    def __init__(self) -> None:
        self.traces = 0
        self.spans = 0

    def generate_trace_id(self) -> int:
        value = derived(f"l3-05/trace/{self.traces}", 16)
        self.traces += 1
        return value

    def generate_span_id(self) -> int:
        value = derived(f"l3-05/span/{self.spans}", 8)
        self.spans += 1
        return value


def sampler_for(name: str) -> Sampler:
    if name == "unbounded-head-10":
        return ParentBased(TraceIdRatioBased(0.10))
    return ParentBased(ALWAYS_ON)


def emit(name: str) -> dict[str, Any]:
    provider = TracerProvider(
        resource=Resource.create({"service.name": "l3-05"}),
        sampler=sampler_for(name),
        id_generator=Sequential(),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{stack.COLLECTOR_HTTP_URL}/v1/traces"),
            max_queue_size=QUEUE_SIZE,
        )
    )
    tracer = provider.get_tracer("l3-05")
    spans: list[dict[str, Any]] = []
    exceptions: list[str] = []
    base = time.time_ns() - 10_000 * MS
    started = time.monotonic()
    for index in range(TRACES):
        route = route_of(index)
        error = is_error(index)
        start = base + index * MS
        try:
            span = tracer.start_span(
                f"GET {route}",
                kind=SpanKind.SERVER,
                start_time=start,
                attributes={"http.route": route, "customer.id": customer_of(index)},
            )
            if error:
                span.set_status(Status(StatusCode.ERROR, "synthetic failure"))
            span.end(end_time=start + (ERROR_MS if error else SUCCESS_MS) * MS)
        except Exception as failure:
            exceptions.append(f"{type(failure).__name__}: {failure}")
            continue
        context = span.get_span_context()
        spans.append(
            {
                "trace_id": format(context.trace_id, "032x"),
                "route": route,
                "error": error,
                "selected": context.trace_flags.sampled,
            }
        )
    emitted = time.monotonic()
    flushed = provider.force_flush()
    try:
        provider.shutdown()
        shutdown = True
    except Exception as failure:
        exceptions.append(f"{type(failure).__name__}: {failure}")
        shutdown = False
    return {
        "spans": spans,
        "emit_seconds": round(emitted - started, 2),
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
