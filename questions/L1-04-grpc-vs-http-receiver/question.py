"""L1-04: what the OTLP receiver costs over gRPC, HTTP protobuf and HTTP JSON."""

import gzip
import http.client
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import grpc
from google.protobuf import json_format
from opentelemetry.proto.collector.metrics.v1 import (
    metrics_service_pb2,
    metrics_service_pb2_grpc,
)

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import wait_for_url
from opentelemetry_experiments.core.runner import Column, Question, Variant

CONFIG = "./questions/L1-04-grpc-vs-http-receiver/config/collector.yaml"
SERVICE = {"service.name": "l1-04"}
METRIC = "l1_04.requests"
REQUESTS = 200
POINTS_PER_REQUEST = 500
POINTS = REQUESTS * POINTS_PER_REQUEST
HOSTS = tuple(f"host-{index:02d}" for index in range(10))
ROUTES = ("/checkout", "/cart", "/search", "/login", "/profile")
START_UNIX_NANO = 1_700_000_000_000_000_000
STEP_NANO = 1_000_000
HOST = "127.0.0.1"
GRPC_PORT = 24317
HTTP_PORT = 24318
INTERFACE = "eth0"
IDLE_SECONDS = 5.0
IDLE_LIMIT = 0.01
POLL_SECONDS = 1.0
DELIVERY_TIMEOUT = 120.0
QUIET_READINGS = 3
REQUEST_TIMEOUT = 10.0


def point(index: int) -> dict[str, Any]:
    return otlp.number_point(
        float(index % 1000),
        {"host.name": HOSTS[index % len(HOSTS)], "route": ROUTES[index % len(ROUTES)]},
        start_unix_nano=START_UNIX_NANO,
        time_unix_nano=START_UNIX_NANO + index * STEP_NANO,
    )


def payload(request: int, points: int = POINTS_PER_REQUEST) -> dict[str, Any]:
    first = request * POINTS_PER_REQUEST
    data = [point(index) for index in range(first, first + points)]
    return otlp.metric_payload(
        [otlp.sum_metric(METRIC, data, temporality=otlp.CUMULATIVE)], SERVICE
    )


def message(document: Mapping[str, Any]) -> Any:
    return json_format.ParseDict(
        document, metrics_service_pb2.ExportMetricsServiceRequest()
    )


VARIANTS = tuple(
    Variant(
        name=name,
        description=description,
        collector_config=CONFIG,
        options={"protocol": protocol, "encoding": encoding, "gzip": compressed},
    )
    for name, description, protocol, encoding, compressed in (
        ("grpc-none", "protobuf over gRPC, no compression", "grpc", "proto", False),
        ("grpc-gzip", "protobuf over gRPC, gzip", "grpc", "proto", True),
        ("http-proto-none", "HTTP protobuf, no compression", "http", "proto", False),
        ("http-proto-gzip", "HTTP protobuf, gzip", "http", "proto", True),
        ("http-json-none", "HTTP JSON, no compression", "http", "json", False),
    )
)


def http_body(
    encoding: str, compressed: bool, document: Mapping[str, Any], parsed: Any
) -> tuple[bytes, dict[str, str]]:
    if encoding == "json":
        body = json.dumps(document, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
    else:
        body = parsed.SerializeToString()
        headers = {"Content-Type": "application/x-protobuf"}
    if compressed:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    return body, headers


def send_http(
    variant: Variant, documents: list[dict[str, Any]], messages: list[Any]
) -> dict[str, Any]:
    connection = http.client.HTTPConnection(HOST, HTTP_PORT, timeout=REQUEST_TIMEOUT)
    statuses: list[int] = []
    sent = 0
    try:
        for document, parsed in zip(documents, messages, strict=True):
            body, headers = http_body(
                variant.options["encoding"], variant.options["gzip"], document, parsed
            )
            connection.request("POST", "/v1/metrics", body=body, headers=headers)
            response = connection.getresponse()
            response.read()
            statuses.append(response.status)
            sent += len(body)
    finally:
        connection.close()
    failed = sum(1 for status in statuses if status != 200)
    return {"failed_requests": failed, "sent_bytes": sent}


def send_grpc(variant: Variant, messages: list[Any]) -> dict[str, Any]:
    compression = grpc.Compression.Gzip if variant.options["gzip"] else None
    failed = 0
    sent = 0
    with grpc.insecure_channel(f"{HOST}:{GRPC_PORT}") as channel:
        stub = metrics_service_pb2_grpc.MetricsServiceStub(channel)
        for parsed in messages:
            try:
                stub.Export(parsed, timeout=REQUEST_TIMEOUT, compression=compression)
            except grpc.RpcError:
                failed += 1
            sent += parsed.ByteSize()
    return {"failed_requests": failed, "sent_bytes": sent}


def json_on_grpc_port() -> str:
    body = json.dumps(payload(0, points=1)).encode()
    connection = http.client.HTTPConnection(HOST, GRPC_PORT, timeout=REQUEST_TIMEOUT)
    try:
        connection.request(
            "POST",
            "/v1/metrics",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read()
        return f"HTTP {response.status}"
    except (OSError, http.client.HTTPException) as error:
        return type(error).__name__
    finally:
        connection.close()


def window(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    delta = stack.counter_delta(before, after)
    problems = list(delta["problems"])
    unexpected = sorted(
        name for name, value in delta["rx_bytes"].items() if name != INTERFACE and value
    )
    if unexpected:
        problems.append(f"bytes arrived on {', '.join(unexpected)}")
    if INTERFACE not in delta["rx_bytes"]:
        problems.append(f"the collector has no {INTERFACE} counter")
    return {
        "cpu_ns": delta["cpu_ns"],
        "rx_bytes": delta["rx_bytes"].get(INTERFACE),
        "problems": problems,
    }


def quiesce(
    read: Callable[[], dict[str, Any]],
    log: Callable[[], str],
    expected: int,
) -> tuple[dict[str, Any], bool]:
    """Wait for the sink log to hold every point, then for Collector RX to stop."""
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    while (
        time.monotonic() < deadline
        and sum(debuglog.export_requests(log(), "metrics")) < expected
    ):
        time.sleep(POLL_SECONDS)
    last = read()
    flat = 0
    while time.monotonic() < deadline and flat < QUIET_READINGS:
        time.sleep(POLL_SECONDS)
        current = read()
        flat = flat + 1 if current["rx_bytes"] == last["rx_bytes"] else 0
        last = current
    return last, flat >= QUIET_READINGS


def per_point(value: float | None) -> float | None:
    return None if value is None else round(value / POINTS, 1)


def summarize(
    idle: Mapping[str, Any],
    work: Mapping[str, Any],
    idle_seconds: float,
    work_seconds: float,
) -> dict[str, Any]:
    cpu_rate = idle["cpu_ns"] / idle_seconds if idle_seconds else 0.0
    rx_rate = (idle["rx_bytes"] or 0) / idle_seconds if idle_seconds else 0.0
    adjusted = work["cpu_ns"] - cpu_rate * work_seconds
    background = rx_rate * work_seconds
    rx = work["rx_bytes"]
    net = None if rx is None else round(rx - background)
    return {
        "idle_rx_bytes": idle["rx_bytes"],
        "rx_bytes": rx,
        "background_rx_bytes": round(background),
        "net_rx_bytes": net,
        "rx_bytes_per_point": per_point(net),
        "collector_cpu_ns": work["cpu_ns"],
        "collector_cpu_ns_per_point": per_point(adjusted),
        "window_problems": [
            f"{phase}: {problem}"
            for phase, values in (("idle", idle), ("workload", work))
            for problem in values["problems"]
        ],
    }


def send(variant: Variant) -> dict[str, Any]:
    env = stack.environment(collector_config=variant.collector_config)
    socket_path = stack.docker_socket()
    engine = stack.engine_version(socket_path)
    documents = [payload(request) for request in range(REQUESTS)]
    messages = [message(document) for document in documents]

    def read() -> dict[str, Any]:
        return stack.raw_stats("collector", env, socket_path)

    prometheus_restored = False
    stack.compose("stop", "prometheus", env=env)
    try:
        start = read()
        time.sleep(IDLE_SECONDS)
        idle_end = read()
        began = time.monotonic()
        cpu_began = time.process_time()
        if variant.options["protocol"] == "grpc":
            result = send_grpc(variant, messages)
        else:
            result = send_http(variant, documents, messages)
        sender_cpu = time.process_time() - cpu_began
        sending_seconds = time.monotonic() - began
        work_end, quiet = quiesce(read, lambda: stack.logs("sink", env), POINTS)
        work_seconds = time.monotonic() - began
    finally:
        stack.compose("start", "prometheus", env=env)
        try:
            wait_for_url(f"{stack.PROMETHEUS_URL}/-/ready")
            prometheus_restored = True
        except TimeoutError:
            prometheus_restored = False
    return {
        "engine": engine,
        "points_sent": POINTS,
        **result,
        "sender_cpu_ns_per_point": per_point(sender_cpu * 1e9),
        "sending_seconds": round(sending_seconds, 2),
        "points_per_second": round(POINTS / sending_seconds),
        "work_seconds": round(work_seconds, 2),
        "quiescent": quiet,
        "prometheus_restored": prometheus_restored,
        "json_on_grpc_port": json_on_grpc_port(),
        **summarize(
            window(start, idle_end),
            window(idle_end, work_end),
            IDLE_SECONDS,
            work_seconds,
        ),
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    requests = debuglog.export_requests(sink_log, "metrics")
    return {"export_requests": len(requests), "delivered_points": sum(requests)}


def expected_points(sending: Mapping[str, Any]) -> int:
    """The probe's one point arrives too when the gRPC port accepts JSON."""
    return POINTS + (1 if sending.get("json_on_grpc_port") == "HTTP 200" else 0)


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    expected = expected_points(sending)
    notes: list[str] = list(sending.get("window_problems", []))
    background = sending.get("background_rx_bytes") or 0
    if background > IDLE_LIMIT * (sending.get("rx_bytes") or 0):
        notes.append(
            f"the collector's idle traffic, {background} bytes over the window, "
            "exceeds 1% of the workload"
        )
    if sending.get("failed_requests"):
        notes.append(f"{sending['failed_requests']} requests failed")
    if not sending.get("quiescent"):
        notes.append("the collector network never went quiet")
    if not sending.get("prometheus_restored"):
        notes.append("Prometheus did not come back")
    if not sending.get("rx_bytes"):
        notes.append("the collector RX counter never moved")
    if run["measured"].get("delivered_points") != expected:
        notes.append(
            f"the sink logged {run['measured'].get('delivered_points')} points"
        )
    sink = run["counters"].get("sink", {}).get("metrics", {})
    if sink.get("receiver_accepted") != expected:
        notes.append(f"the sink accepted {sink.get('receiver_accepted')} points")
    return notes


QUESTION = Question(
    id="L1-04",
    title="What does choosing gRPC over HTTP on the receiver cost or save?",
    level=1,
    theme="collector",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=5,
    rotate_variants=True,
    columns=(
        Column("Points sent", "sending.points_sent"),
        Column("Sent bytes", "sending.sent_bytes"),
        Column("Collector RX bytes", "sending.rx_bytes"),
        Column("Background RX bytes", "sending.background_rx_bytes"),
        Column("Net RX bytes", "sending.net_rx_bytes"),
        Column("RX bytes per point", "sending.rx_bytes_per_point", digits=1),
        Column(
            "Collector CPU ns per point", "sending.collector_cpu_ns_per_point", digits=1
        ),
        Column("Sender CPU ns per point", "sending.sender_cpu_ns_per_point", digits=1),
        Column("Points per second", "sending.points_per_second"),
        Column("Failed requests", "sending.failed_requests"),
        Column("Export requests", "measured.export_requests"),
        Column("Delivered", "measured.delivered_points"),
        Column("Collector accepted", "counters.collector.metrics.receiver_accepted"),
        Column("Sink accepted", "counters.sink.metrics.receiver_accepted"),
        Column("JSON on the gRPC port", "sending.json_on_grpc_port", kind="text"),
        Column("Order position", "position"),
        Column("Warn lines", "log.warn_lines"),
        Column("Error lines", "log.error_lines"),
    ),
    interpretation="""
The protocol barely changes the bytes, the encoding and compression do. For
100000 points, uncompressed gRPC and HTTP protobuf reached the Collector as
74.0 and 73.9 bytes per point, 0.23% apart. gzip took both to under 9 bytes
per point, 88% less, and HTTP JSON cost 218.5, 2.96 times protobuf.

The Collector paid for what it had to undo. JSON cost it 1586.7 ns per point
against 836.5 for HTTP protobuf, 1.90 times, and gzip added 32% to 38%. The
sender paid more: gzip raised its CPU per point by 82% over gRPC and by 627%
over HTTP, and on loopback cut throughput by 25% and 54%. Uncompressed HTTP
protobuf was the fastest and the cheapest for this sender.

The README's "the HTTP/JSON address is the same as gRPC" did not hold: all 25
JSON requests to the gRPC port failed with `BadStatusLine`.

What this does to a bill: on a billed link, gzip divides receiver ingress by
about 8.5 for a third more Collector CPU. JSON triples the bytes and doubles
the Collector's CPU, so it belongs where a browser or a script cannot speak
protobuf, not on a service's hot path.
""",
    does_not_say="""
One Python sender over loopback, in sequence, so sender CPU and throughput
describe `grpcio` and Python's `gzip`, not an SDK exporter or a real network.
Collector RX is one container interface behind Docker Desktop's port forwarding,
not billable ingress, with no TLS, proxy or load balancer. zstd, snappy,
concurrent senders and gzipped JSON were not run.
""",
)
