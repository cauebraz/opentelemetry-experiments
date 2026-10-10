"""L2-02: the same memory pressure against memory_limiter before and after batch."""

import re
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l2-02"}
CONFIG = "./questions/L2-02-memory-limiter-placement/config"
MEMORY_CEILING = "512m"

RECORDS_PER_REQUEST = 10
PADDING_BYTES = 64 * 1024
INTERVAL = 0.05
PRESSURE_REQUESTS = 200
CONTROL_REQUESTS = 20
HOLD_SECONDS = 5.0
DRAIN_TIMEOUT = 240.0
QUIET_SAMPLES = 35

REFUSING = re.compile(r"Memory usage is above soft limit\. Refusing data\.")
SENDER_FAILED = re.compile(r"Sender failed")
MIB = 1024 * 1024


def variant(name: str, description: str, config: str, requests: int) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{CONFIG}/{config}",
        sink_config=f"{CONFIG}/sink.yaml",
        collector_memory_limit=MEMORY_CEILING,
        options={"requests": requests, "pressure": requests > CONTROL_REQUESTS},
    )


VARIANTS = (
    variant(
        "control",
        "memory_limiter first, 20 requests, the heap stays under the soft limit",
        "collector-limiter-first.yaml",
        CONTROL_REQUESTS,
    ),
    variant(
        "limiter-first",
        "memory_limiter then batch, up to 200 requests with the sink stopped",
        "collector-limiter-first.yaml",
        PRESSURE_REQUESTS,
    ),
    variant(
        "batch-first",
        "batch then memory_limiter, up to 200 requests with the sink stopped",
        "collector-batch-first.yaml",
        PRESSURE_REQUESTS,
    ),
)


def record_id(request: int, index: int) -> str:
    return f"r{request:04d}-i{index:02d}"


def request_ids(request: int) -> list[str]:
    return [record_id(request, index) for index in range(RECORDS_PER_REQUEST)]


def request_payload(request: int, padding: str) -> dict[str, Any]:
    return otlp.log_payload(
        [
            otlp.log_record(
                body,
                time_unix_nano=otlp.now_unix_nano(),
                record_attributes={"padding": padding},
            )
            for body in request_ids(request)
        ],
        SERVICE,
    )


def collector_value(expression: str) -> float | None:
    return query_value(stack.PROMETHEUS_URL, expression)


def peak_mib(metric: str) -> float | None:
    value = collector_value(
        f'max(max_over_time({{__name__="{metric}", job="collector"}}[10m]))'
    )
    return None if value is None else round(value / MIB, 1)


def sink_accepted() -> float | None:
    return collector_value(
        'sum({__name__=~"otelcol_receiver_accepted_log_records(_total)?", job="sink"})'
    )


def queue_size() -> float | None:
    return collector_value(
        'max({__name__="otelcol_exporter_queue_size", job="collector"})'
    )


def wait_for_drain(expected: int, started: float) -> tuple[float | None, float | None]:
    """Sink count once the queue is empty and the count stopped rising.

    Reaching every acknowledged record ends the wait after two agreeing
    samples; otherwise a retry can sit idle for `max_interval`, so the count is
    only final after 35 quiet seconds.
    """
    previous = None
    last_change = None
    repeats = 0
    while time.monotonic() - started < DRAIN_TIMEOUT:
        current = sink_accepted()
        empty = queue_size() == 0
        if current is not None and current != previous:
            last_change = time.monotonic()
        repeats = repeats + 1 if current is not None and current == previous else 0
        complete = current is not None and current >= expected
        if empty and repeats >= (2 if complete else QUIET_SAMPLES):
            drained = None if last_change is None else round(last_change - started, 1)
            return current, drained
        previous = current
        time.sleep(1.0)
    raise TimeoutError(
        f"the queue did not drain after {DRAIN_TIMEOUT:g}s; "
        f"last sink sample was {previous}"
    )


def percentile(values: Sequence[float], share: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return round(cuts[int(share * 100) - 1], 1)


def send(variant: Variant) -> dict[str, Any]:
    environment = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
        collector_memory_limit=variant.collector_memory_limit,
    )
    padding = "x" * PADDING_BYTES
    stack.stop_sink(environment)
    statuses: list[int] = []
    latencies: list[float] = []
    first_refused = None
    for request in range(variant.options["requests"]):
        started = time.monotonic()
        result = otlp.post_json(
            stack.COLLECTOR_HTTP_URL, "logs", request_payload(request, padding)
        )
        latencies.append((time.monotonic() - started) * 1000)
        statuses.append(result["http_status"])
        if result["http_status"] != 200:
            first_refused = request
            break
        time.sleep(INTERVAL)
    time.sleep(HOLD_SECONDS)
    heap_peak = peak_mib("otelcol_process_runtime_heap_alloc_bytes")
    rss_peak = peak_mib("otelcol_process_memory_rss")
    queue_max = collector_value(
        'max(max_over_time({__name__="otelcol_exporter_queue_size",'
        ' job="collector"}[10m]))'
    )
    acknowledged = statuses.count(200) * RECORDS_PER_REQUEST
    recovery = time.monotonic()
    stack.start_sink(environment)
    at_quiet, drain_seconds = wait_for_drain(acknowledged, recovery)
    state = stack.container_state("collector", environment)
    return {
        "requests_sent": len(statuses),
        "records_sent": len(statuses) * RECORDS_PER_REQUEST,
        "statuses": statuses,
        "status_counts": "; ".join(
            f"{status}:{count}" for status, count in sorted(Counter(statuses).items())
        ),
        "first_refused_request": first_refused,
        "first_refused_status": None if first_refused is None else statuses[-1],
        "latency_ms_p50": percentile(latencies, 0.5),
        "latency_ms_max": round(max(latencies), 1),
        "heap_peak_mib": heap_peak,
        "rss_peak_mib": rss_peak,
        "queue_size_max": queue_max,
        "limiter_refused": collector_value(
            'sum({__name__=~"otelcol_processor_memory_limiter_refused_log_records'
            '(_total)?", job="collector"})'
        ),
        "sink_at_quiet": at_quiet,
        "drain_seconds": drain_seconds,
        "running": state["running"],
        "oom_killed": state["oom_killed"],
        "restarts": state["restarts"],
    }


def accounting(statuses: Sequence[int], delivered: Sequence[str]) -> dict[str, Any]:
    """Which records the sender was told about and what became of them."""
    acked = {
        body
        for request, status in enumerate(statuses)
        if status == 200
        for body in request_ids(request)
    }
    refused = {
        body
        for request, status in enumerate(statuses)
        if status != 200
        for body in request_ids(request)
    }
    seen = Counter(delivered)
    unique = set(seen)
    return {
        "delivered_unique": len(unique),
        "duplicates": sum(count - 1 for count in seen.values()),
        "acked_not_delivered": len(acked - unique),
        "refused_records": len(refused),
        "refused_delivered": len(refused & unique),
        "unknown_delivered": len(unique - acked - refused),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    collector = stack.unprefixed(collector_log)
    return {
        **accounting(sending["statuses"], debuglog.log_bodies(sink_log)),
        "records_printed": len(debuglog.log_bodies(sink_log)),
        "refusing_lines": len(REFUSING.findall(collector)),
        "sender_failed_lines": len(SENDER_FAILED.findall(collector)),
    }


def engaged(run: Mapping[str, Any]) -> bool:
    refused = run["sending"].get("limiter_refused") or 0
    return refused > 0 or run["measured"].get("refusing_lines", 0) > 0


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes = []
    if not sending["running"] or sending["oom_killed"] or sending["restarts"]:
        notes.append(
            "the collector did not survive the run: "
            f"running {sending['running']}, OOM killed {sending['oom_killed']}, "
            f"restarts {sending['restarts']}"
        )
    printed = measured["records_printed"]
    if sending["sink_at_quiet"] != printed:
        notes.append(
            f"the sink counted {sending['sink_at_quiet']} records but printed {printed}"
        )
    if measured["unknown_delivered"]:
        notes.append(
            f"the sink printed {measured['unknown_delivered']} ids that were never sent"
        )
    if variant.options["pressure"] and not engaged(run):
        notes.append("the limiter never engaged in a pressure variant")
    if not variant.options["pressure"] and engaged(run):
        notes.append("the limiter engaged in the control")
    return notes


QUESTION = Question(
    id="L2-02",
    title="Does memory_limiter belong before or after batch?",
    level=2,
    theme="collector",
    signals=("logs",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("Requests sent", "sending.requests_sent"),
        Column("Records sent", "sending.records_sent"),
        Column("Statuses", "sending.status_counts", kind="text"),
        Column("First refused request", "sending.first_refused_request"),
        Column("Accepted", "counters.collector.logs.receiver_accepted"),
        Column("Receiver refused", "counters.collector.logs.receiver_refused"),
        Column("Limiter refused", "sending.limiter_refused"),
        Column("Heap peak MiB", "sending.heap_peak_mib", digits=1),
        Column("RSS peak MiB", "sending.rss_peak_mib", digits=1),
        Column("Queue size max", "sending.queue_size_max"),
        Column("Latency p50 ms", "sending.latency_ms_p50", digits=1),
        Column("Latency max ms", "sending.latency_ms_max", digits=1),
        Column("Sink accepted", "counters.sink.logs.receiver_accepted"),
        Column("Delivered", "measured.delivered_unique"),
        Column("Duplicates", "measured.duplicates"),
        Column("Acked, not delivered", "measured.acked_not_delivered"),
        Column("Refused records", "measured.refused_records"),
        Column("Refusing lines", "measured.refusing_lines"),
        Column("Sender failed lines", "measured.sender_failed_lines"),
        Column("Seconds to drain", "sending.drain_seconds", digits=1),
        Column("OOM killed", "sending.oom_killed", kind="text"),
    ),
    interpretation="""
With batch in front, 1130 to 1140 records were answered with HTTP 200 and never
arrived. All 200 requests got 200, the receiver refused 0 records, and the sink
received 860 to 870 of the 2000 sent. The limiter refused the batch processor,
the batch processor logged `Sender failed` with
`"error": "data refused due to high memory usage"` at `warn`, and nothing
retried. The sender was told every record was accepted.

With the limiter first, nothing acknowledged was lost. The heap crossed the soft
limit after 55 to 57 requests, the next request got HTTP 503, and the receiver
counted those 10 records as refused. Every record of every request answered 200
reached the sink after recovery, in all three repetitions. The loss is exactly
the refused request, and the sender holds a status code that says so.

The one counter that sees the loss in both orders is
`otelcol_processor_memory_limiter_refused_log_records`. Under `batch-first` it
equals the acknowledged records that never arrived, 1140, 1130 and 1140, run
for run. Under `limiter-first` it reads 10, the refused request. The metric does
not exist in the control, where the limiter never refused.

The `Sender failed` line carries no item count and appeared exactly 10 times in
each `batch-first` run, the Collector log sampler's first-ten allowance. It
says that a loss happened, not how large it was.

The peak heap failed the author's prediction. `batch-first` peaked at 104.9 to
111.9 MiB on the one second scrape, above the 96 MiB hard limit, while
`limiter-first` peaked at 84.9 to 90.2 MiB. The batch processor accepts and
holds data before the limiter can refuse it, so the limiter acts later and the
heap goes further. RSS reached 290.1 to 293.3 MiB, under the 512m container
ceiling, and no run was OOM killed.

Latency did not separate the orders: median round trip 7.4 to 9.0 ms in every
variant. A 503 is the only thing the sender can see.

What this does to a pipeline: put `memory_limiter` first, and alert on
`otelcol_processor_memory_limiter_refused_*` as data loss whenever any
processor sits in front of the limiter. Behind batch, that counter is the only
number that measures what was acknowledged and dropped.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with logs only, one OTLP/HTTP receiver,
`memory_limiter` at `limit_mib: 96` and `spike_limit_mib: 24`, the batch
processor at its defaults, and a sink stopped for the whole sending phase.
Requests of 10 records with a 64 KiB attribute each, 50 ms apart. The pressure
comes from the exporter queue holding undeliverable data; a burst of large
requests against a healthy destination is a different shape of pressure.

The sender here stops at the first non-200 status and does not retry. A client
that retries a 503, as the OTLP specification asks, would turn the 10 refused
records of `limiter-first` into delayed delivery, but that client was not run.

The OTLP/gRPC receiver, traces and metrics, other processors in front of the
limiter, `memory_limiter` as an extension on the receiver, and a persistent
queue are not tested. Heap and RSS peaks come from a one second scrape and can
miss a shorter peak. Log line counts are floors under the Collector's default
sampler.
""",
)
