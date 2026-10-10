"""L3-06: bytes and CPU per datapoint over gRPC and HTTP, with and without gzip."""

import time
from collections.abc import Callable, Mapping
from typing import Any

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import wait_for_url
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L3-06-compression-and-protocol/config"
SINK = f"{DIRECTORY}/sink.yaml"
SERVICE = {"service.name": "l3-06"}
METRIC = "l3_06.requests"
REQUESTS = 200
POINTS_PER_REQUEST = 500
POINTS = REQUESTS * POINTS_PER_REQUEST
BATCH = 5000
HOSTS = tuple(f"host-{index:02d}" for index in range(10))
ROUTES = ("/checkout", "/cart", "/search", "/login", "/profile")
START_UNIX_NANO = 1_700_000_000_000_000_000
STEP_NANO = 1_000_000
INTERFACE = "eth0"
IDLE_SECONDS = 5.0
POLL_SECONDS = 1.0
DELIVERY_TIMEOUT = 120.0
QUIET_READINGS = 3
IDLE_LIMIT = 0.01


def point(index: int) -> dict[str, Any]:
    return otlp.number_point(
        float(index % 1000),
        {"host.name": HOSTS[index % len(HOSTS)], "route": ROUTES[index % len(ROUTES)]},
        start_unix_nano=START_UNIX_NANO,
        time_unix_nano=START_UNIX_NANO + index * STEP_NANO,
    )


def payload(request: int) -> dict[str, Any]:
    first = request * POINTS_PER_REQUEST
    points = [point(index) for index in range(first, first + POINTS_PER_REQUEST)]
    return otlp.metric_payload(
        [otlp.sum_metric(METRIC, points, temporality=otlp.CUMULATIVE)], SERVICE
    )


VARIANTS = tuple(
    Variant(
        name=f"{protocol}-{compression}",
        description=description,
        collector_config=f"{DIRECTORY}/collector-{protocol}-{compression}.yaml",
        sink_config=SINK,
    )
    for protocol, compression, description in (
        ("grpc", "none", "OTLP/gRPC to the sink, compression none"),
        ("grpc", "gzip", "OTLP/gRPC to the sink, compression gzip"),
        ("http", "none", "OTLP/HTTP protobuf to the sink, compression none"),
        ("http", "gzip", "OTLP/HTTP protobuf to the sink, compression gzip"),
    )
)


def environment(variant: Variant) -> dict[str, str]:
    return stack.environment(
        collector_config=variant.collector_config, sink_config=variant.sink_config
    )


def delivered(sink_log: str) -> list[int]:
    return debuglog.export_requests(sink_log, "metrics")


def window(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    delta = stack.counter_delta(before, after)
    problems = list(delta["problems"])
    unexpected = sorted(
        name for name, value in delta["rx_bytes"].items() if name != INTERFACE and value
    )
    if unexpected:
        problems.append(f"bytes arrived on {', '.join(unexpected)}")
    if INTERFACE not in delta["rx_bytes"]:
        problems.append(f"the sink has no {INTERFACE} counter")
    return {
        "cpu_ns": delta["cpu_ns"],
        "rx_bytes": delta["rx_bytes"].get(INTERFACE),
        "problems": problems,
    }


def readings(env: dict[str, str], socket_path: str) -> dict[str, dict[str, Any]]:
    return {
        "collector": stack.raw_stats("collector", env, socket_path),
        "sink": stack.raw_stats("sink", env, socket_path),
    }


def quiesce(
    read: Callable[[], dict[str, dict[str, Any]]],
    log: Callable[[], str],
    expected: int,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Wait for the sink log to hold every point, then for RX to stop moving."""
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    while time.monotonic() < deadline and sum(delivered(log())) < expected:
        time.sleep(POLL_SECONDS)
    last = read()
    flat = 0
    while time.monotonic() < deadline and flat < QUIET_READINGS:
        time.sleep(POLL_SECONDS)
        current = read()
        same = current["sink"]["rx_bytes"] == last["sink"]["rx_bytes"]
        flat = flat + 1 if same else 0
        last = current
    return last, flat >= QUIET_READINGS


def per_point(value: float | None) -> float | None:
    return None if value is None else round(value / POINTS, 1)


def summarize_windows(
    idle: Mapping[str, Mapping[str, Any]],
    work: Mapping[str, Mapping[str, Any]],
    idle_seconds: float,
    work_seconds: float,
) -> dict[str, Any]:
    idle_rate = idle["collector"]["cpu_ns"] / idle_seconds if idle_seconds else 0.0
    adjusted = work["collector"]["cpu_ns"] - idle_rate * work_seconds
    rx = work["sink"]["rx_bytes"]
    idle_rx = idle["sink"]["rx_bytes"] or 0
    background = idle_rx / idle_seconds * work_seconds if idle_seconds else 0.0
    net = None if rx is None else round(rx - background)
    return {
        "idle_sink_rx_bytes": idle["sink"]["rx_bytes"],
        "idle_collector_cpu_ns": idle["collector"]["cpu_ns"],
        "sink_rx_bytes": rx,
        "background_rx_bytes": round(background),
        "net_rx_bytes": net,
        "rx_bytes_per_point": per_point(net),
        "collector_cpu_ns": work["collector"]["cpu_ns"],
        "adjusted_cpu_ns": round(adjusted),
        "adjusted_cpu_ns_per_point": per_point(adjusted),
        "window_problems": [
            f"{phase} {role}: {problem}"
            for phase, windows in (("idle", idle), ("workload", work))
            for role, values in windows.items()
            for problem in values["problems"]
        ],
    }


def send(variant: Variant) -> dict[str, Any]:
    env = environment(variant)
    socket_path = stack.docker_socket()
    engine = stack.engine_version(socket_path)
    statuses: list[int] = []
    request_bytes = 0
    prometheus_restored = False
    stack.compose("stop", "prometheus", env=env)
    try:
        start = readings(env, socket_path)
        time.sleep(IDLE_SECONDS)
        idle_end = readings(env, socket_path)
        began = time.monotonic()
        for request in range(REQUESTS):
            result = otlp.post_json(
                stack.COLLECTOR_HTTP_URL, "metrics", payload(request)
            )
            statuses.append(result["http_status"])
            request_bytes += result["request_bytes"]
        work_end, quiet = quiesce(
            lambda: readings(env, socket_path),
            lambda: stack.logs("sink", env),
            POINTS,
        )
        elapsed = time.monotonic() - began
    finally:
        stack.compose("start", "prometheus", env=env)
        try:
            wait_for_url(f"{stack.PROMETHEUS_URL}/-/ready")
            prometheus_restored = True
        except TimeoutError:
            prometheus_restored = False
    idle = {role: window(start[role], idle_end[role]) for role in start}
    work = {role: window(idle_end[role], work_end[role]) for role in start}
    return {
        "engine": engine,
        "points_sent": POINTS,
        "requests_sent": REQUESTS,
        "non_200": sum(1 for status in statuses if status != 200),
        "input_request_bytes": request_bytes,
        "idle_seconds": IDLE_SECONDS,
        "work_seconds": round(elapsed, 2),
        "quiescent": quiet,
        "prometheus_restored": prometheus_restored,
        **summarize_windows(idle, work, IDLE_SECONDS, elapsed),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    requests = delivered(sink_log)
    return {
        "export_requests": len(requests),
        "delivered_points": sum(requests),
        "largest_request": max(requests, default=0),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    counters = run["counters"]
    notes: list[str] = list(sending.get("window_problems", []))
    background = sending.get("background_rx_bytes") or 0
    if background > IDLE_LIMIT * (sending.get("sink_rx_bytes") or 0):
        notes.append(
            f"the sink's idle traffic, {background} bytes over the window, "
            "exceeds 1% of the workload"
        )
    if sending.get("non_200"):
        notes.append(f"{sending['non_200']} input requests were not answered 200")
    if not sending.get("quiescent"):
        notes.append("the sink network never went quiet")
    if not sending.get("prometheus_restored"):
        notes.append("Prometheus did not come back")
    if not sending.get("sink_rx_bytes"):
        notes.append("the sink RX counter never moved")
    if measured.get("delivered_points") != POINTS:
        notes.append(f"the sink logged {measured.get('delivered_points')} points")
    if measured.get("largest_request", 0) > BATCH:
        notes.append(f"an export request carried {measured['largest_request']} points")
    collector = counters.get("collector", {}).get("metrics", {})
    sink = counters.get("sink", {}).get("metrics", {})
    if collector.get("receiver_accepted") != POINTS:
        notes.append(
            f"the collector accepted {collector.get('receiver_accepted')} points"
        )
    if sink.get("receiver_accepted") != POINTS:
        notes.append(f"the sink accepted {sink.get('receiver_accepted')} points")
    for name in ("receiver_refused", "exporter_send_failed"):
        if collector.get(name):
            notes.append(f"the collector reported {name} {collector[name]:g}")
    return notes


QUESTION = Question(
    id="L3-06",
    title="How many bytes does a datapoint cost on the wire?",
    level=3,
    theme="collector",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=4,
    rotate_variants=True,
    columns=(
        Column("Points sent", "sending.points_sent"),
        Column("Input request bytes", "sending.input_request_bytes"),
        Column("Sink RX bytes", "sending.sink_rx_bytes"),
        Column("RX bytes per point", "sending.rx_bytes_per_point", digits=1),
        Column("Collector CPU ns", "sending.collector_cpu_ns"),
        Column(
            "Adjusted CPU ns per point", "sending.adjusted_cpu_ns_per_point", digits=1
        ),
        Column("Idle sink RX bytes", "sending.idle_sink_rx_bytes"),
        Column("Background RX bytes", "sending.background_rx_bytes"),
        Column("Net RX bytes", "sending.net_rx_bytes"),
        Column("Work seconds", "sending.work_seconds", digits=2),
        Column("Export requests", "measured.export_requests"),
        Column("Delivered", "measured.delivered_points"),
        Column("Collector accepted", "counters.collector.metrics.receiver_accepted"),
        Column("Sink accepted", "counters.sink.metrics.receiver_accepted"),
        Column("Send failed", "counters.collector.metrics.exporter_send_failed"),
        Column("Order position", "position"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
    ),
    interpretation="""
Compression decides the bytes, and the protocol does not. Without compression,
OTLP/gRPC and OTLP/HTTP protobuf delivered 100000 points as 7338654 and 7339793
bytes at the sink's interface, 73.4 bytes per point, 0.02% apart. gzip took
both to 8.4 bytes per point, 88.5% less.

The bytes saved cost CPU. Idle-adjusted Collector CPU rose from 1802.7 to
2150.2 ns per point on gRPC, 19.3%, and from 1825.9 to 2245.3 on HTTP, 23.0%.
Every gzip run sat above every uncompressed run of its protocol, across four
balanced orders.

What this does to a bill: on a link where bytes are billed, gzip divides the
telemetry by about 8.7 for roughly a fifth more Collector CPU. Two pages at the
same commit disagree about the default, so the setting belongs in the config.
""",
    does_not_say="""
The CPU figure covers the whole Collector, including decoding the sender's
23.5 MB of OTLP/JSON; only the differences are compression. Sink RX is one
container interface on a Docker Desktop bridge, with TCP and HTTP framing. It
is not billable egress, and there is no TLS, proxy or load balancer. The
payload is one repetitive metric, which favours gzip; zstd, snappy, other batch
sizes and the receiver's CPU were not run.
""",
)
