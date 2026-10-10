"""L2-03: which trigger sent each batch, and what the two settings cost."""

import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import counters_by_exporter, query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l2-03"}
METRIC_NAME = "l2_03.requests"
SINK_EXPORTER = "otlp_grpc/sink"

REQUESTS = 100
INTERVAL = 0.05
COMPLETE_TIMEOUT = 40.0
TIMER_IDLE_SECONDS = (0.0, 3.0, 7.0)

CONFIG = "./questions/L2-03-batch-sizing/config"

RUNS_BY_VARIANT: dict[str, int] = {}

VARIANTS = (
    Variant(
        name="no-batch",
        description="no batch processor in the pipeline",
        collector_config=f"{CONFIG}/collector-no-batch.yaml",
    ),
    Variant(
        name="defaults",
        description="batch at its defaults, 8192 datapoints or 200ms",
        collector_config=f"{CONFIG}/collector-defaults.yaml",
    ),
    Variant(
        name="size-1",
        description="send_batch_size 1, timeout 200ms",
        collector_config=f"{CONFIG}/collector-size-1.yaml",
    ),
    Variant(
        name="size-5-timeout-10s",
        description="send_batch_size 5, timeout 10s",
        collector_config=f"{CONFIG}/collector-size-5-timeout-10s.yaml",
    ),
    Variant(
        name="size-8192-timeout-10s",
        description="send_batch_size 8192, timeout 10s",
        collector_config=f"{CONFIG}/collector-size-8192-timeout-10s.yaml",
    ),
)


def one_datapoint(index: int) -> dict[str, Any]:
    moment = otlp.now_unix_nano()
    point = otlp.number_point(
        1.0,
        {"http.route": f"/route-{index:03d}"},
        start_unix_nano=moment - 1_000_000_000,
        time_unix_nano=moment,
    )
    return otlp.metric_payload([otlp.sum_metric(METRIC_NAME, [point])], SERVICE)


def arrived_at_sink() -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        'sum({__name__=~"otelcol_receiver_accepted_metric_points(_total)?",'
        ' job="sink"})',
    )


def wait_for_arrival(target: float, started: float) -> float | None:
    """Seconds from the first request until the target arrived and stopped rising.

    Prometheus scrapes every second, so the resolution of this number is one
    second whatever the poll interval is.
    """
    previous: float | None = None
    while time.monotonic() - started < COMPLETE_TIMEOUT:
        current = arrived_at_sink()
        if current is not None and current >= target and current == previous:
            return round(time.monotonic() - started)
        previous = current
        time.sleep(0.2)
    return None


def send(variant: Variant) -> dict[str, Any]:
    """One datapoint per request, so the batch processor sees 100 arrivals."""
    run_index = RUNS_BY_VARIANT.get(variant.name, 0)
    RUNS_BY_VARIANT[variant.name] = run_index + 1
    pre_send_idle = (
        TIMER_IDLE_SECONDS[run_index % len(TIMER_IDLE_SECONDS)]
        if variant.name == "size-8192-timeout-10s"
        else 0.0
    )
    time.sleep(pre_send_idle)
    started = time.monotonic()
    statuses = set()
    for index in range(REQUESTS):
        result = otlp.post_json(
            stack.COLLECTOR_HTTP_URL,
            "metrics",
            one_datapoint(index),
        )
        statuses.add(result["http_status"])
        time.sleep(INTERVAL)
    return {
        "http_status": max(statuses),
        "requests_sent": REQUESTS,
        "datapoints_sent": REQUESTS,
        "pre_send_idle": pre_send_idle,
        "seconds_sending": round(time.monotonic() - started, 1),
        "seconds_to_complete": wait_for_arrival(REQUESTS, started),
    }


def batch_counter(name: str) -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        f'sum({{__name__="otelcol_processor_batch_{name}", job="collector"}})',
    )


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    by_exporter = counters_by_exporter(stack.PROMETHEUS_URL, "collector", "metrics")
    requests = debuglog.export_requests(sink_log, "metrics")
    batches = batch_counter("batch_send_size_count")
    datapoints_batched = batch_counter("batch_send_size_sum")
    return {
        "sent_to_sink": by_exporter.get(SINK_EXPORTER),
        "size_triggers": batch_counter("batch_size_trigger_send"),
        "timeout_triggers": batch_counter("timeout_trigger_send"),
        "batches_sent": batches,
        "mean_batch_size": (
            round(datapoints_batched / batches, 1)
            if batches and datapoints_batched is not None
            else None
        ),
        "bytes_batched": batch_counter("batch_send_size_bytes_sum"),
        "requests_at_sink": len(requests) or None,
        "largest_request_at_sink": max(requests, default=None),
        "seconds_to_complete": sending["seconds_to_complete"],
    }


QUESTION = Question(
    id="L2-03",
    title="When does batch actually send, and what does each setting cost?",
    level=2,
    theme="collector",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Requests sent", "sending.requests_sent"),
        Column("Pre-send idle", "sending.pre_send_idle"),
        Column("Accepted", "counters.collector.metrics.receiver_accepted"),
        Column("Size triggers", "measured.size_triggers"),
        Column("Timer triggers", "measured.timeout_triggers"),
        Column("Batches sent", "measured.batches_sent"),
        Column("Mean batch size", "measured.mean_batch_size", digits=1),
        Column("Bytes batched", "measured.bytes_batched"),
        Column("Requests at the sink", "measured.requests_at_sink"),
        Column("Largest request", "measured.largest_request_at_sink"),
        Column("Sent to the sink", "measured.sent_to_sink"),
        Column("Arrived at the sink", "counters.sink.metrics.receiver_accepted"),
        Column("Seconds to complete", "measured.seconds_to_complete"),
    ),
    interpretation="""
The two triggers never competed. In every variant one of them fired and the
other published no series at all, and the one that fired accounts for every
batch: `size_triggers` plus `timeout_triggers` equals `batches_sent` in all
five variants, and `batches_sent` equals the number of export requests the sink
logged.

Which one fires is decided by the arrival rate, not by the configuration alone.
The same 100 datapoints, arriving one per request 50ms apart over 5.6 seconds,
produce 100 size-triggered sends at `send_batch_size: 1`, 20 at
`send_batch_size: 5`, and 0 at `send_batch_size: 8192`, where the timer sends
instead. At the defaults the timer sends 28 or 29 times, which is 5.6 seconds
divided by the 200ms timeout, and the mean batch holds 3.4 to 3.6 datapoints.
A `send_batch_size` above the arrival rate times the timeout is not a batch
size, it is an unused branch.

The timer is process-relative, so the central prediction failed. The
`size-8192-timeout-10s` repetitions waited 0, 3 and 7 seconds after readiness
before the first request, then completed 11, 8 and 14 seconds after that
request. With 0 seconds idle, one tick sent all 100 datapoints. With 3 seconds
idle, that tick arrived about 7 seconds after the first item and still sent all
100. With 7 seconds idle, the first tick interrupted the 5.6 second send and a
second tick was needed, so the run emitted two requests of 50 datapoints on
average.

`timeout` is therefore an upper bound on how long a pending item waits for a
process-relative tick, not a delay counted from the first item. The phase can
also split one workload into two export requests without any change in arrival
rate or batch settings.

Request count follows the trigger exactly. `no-batch` put 100 requests on the
wire for 100 datapoints, `size-5-timeout-10s` put 20, and
`size-8192-timeout-10s` put 1. That is the number that costs money on a billed
endpoint and connections on a shared one, and the sink's log counts it directly
because the sink runs with log sampling off.

The cost prediction failed, and it failed in a way worth knowing.
`otelcol_processor_batch_batch_send_size_bytes_sum` reads 14500 in every
variant that has a batch processor: at 1 datapoint per batch and at 100, the
same 100 datapoints, the same 145 bytes each. The histogram sums the bytes of
the items, before they become a request, so it does not see the saving that
batching exists for. A dashboard built on it shows batching as free and as
useless at the same time. `no-batch` publishes no bytes series, so removing the
processor also removes the only byte figure the pipeline reported.

What this does to a dashboard: read the two trigger counters together, because
the quiet one is quiet by being absent, not by reading zero. A panel on
`timeout_trigger_send` alone shows nothing at all when `send_batch_size` is
doing the work. When the timer is in control, its counter says how many process
ticks found data, while end-to-end latency still depends on the workload's phase
against those ticks.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with one metrics pipeline, 100 OTLP/HTTP
requests of one datapoint each 50ms apart, an OTLP gRPC destination with the
default queue and retry, and telemetry level `detailed`. A different arrival
rate moves every number in the table: the trigger that fires is a function of
the rate against the configuration, and this run holds the rate constant.

The claim that `send_batch_size` "acts as a trigger and does not affect the
size of the batch" was not exercised. Items arrived one at a time, so no batch
could overshoot its trigger, and `largest_request` never exceeded
`send_batch_size`. Whether a single request of 1000 datapoints is split, or
sent whole past a `send_batch_size` of 5, needs requests larger than one item
and a `send_batch_max_size` variant.

`seconds_to_complete` is accurate to one second, because Prometheus scrapes the
sink every second. It measures when the datapoints were counted at the sink,
not when the Collector called the exporter. The 8 to 14 second range and the
second trigger after 7 seconds idle distinguish a process-relative ticker from
a timer started by the first item; they do not locate that ticker in the
Collector's code.

The bytes figure is the batch processor's own histogram at telemetry level
`detailed`. It is not the size of the gRPC request, not compressed, and not
what a network bill would count. No compression variant was run, and the wire
size was never measured here.

Nothing failed in these runs. Every variant delivered 100 datapoints with HTTP
200 and no retry, so this says nothing about how a batch behaves when its
export fails, which is L2-05, or about `send_batch_max_size`, `metadata_keys`
and the exporter's own batching, none of which were configured.
""",
)
