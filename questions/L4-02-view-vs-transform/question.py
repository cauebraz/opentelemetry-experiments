"""L4-02: SDK View attribute_keys versus Collector aggregate_on_attributes."""

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import debuglog, stack
from opentelemetry_experiments.core.prometheus import (
    query_result,
    query_value,
    wait_for_url,
)
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L4-02-view-vs-transform"
EMITTER = stack.ROOT / "questions" / "L4-02-view-vs-transform" / "emitter.py"
EMITTER_TIMEOUT = 120.0
INTERFACE = "eth0"
IDLE_SECONDS = 5.0
POLL_SECONDS = 1.0
DELIVERY_TIMEOUT = 90.0
QUIET_READINGS = 3
OBSERVATIONS = 600
EXPECTED_BASELINE_IDENTITIES = 200
EXPECTED_REDUCED_IDENTITIES = 20
FAMILY_PER_IDENTITY = 11
METRIC_COUNT = '{__name__=~"http_server_duration_milliseconds_count", job="exported"}'
METRIC_BUCKET = '{__name__=~"http_server_duration_milliseconds_bucket", job="exported"}'
INBOUND = '{__name__=~"l4_02_inbound(_total)?", job="exported"}'
USER_LABEL = "user_id"


def variant(name: str, description: str, config: str, use_view: bool) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{DIRECTORY}/config/{config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
        options={"use_view": use_view},
    )


VARIANTS = (
    variant(
        "baseline",
        "full attributes, no SDK view, no Collector transform",
        "collector-baseline.yaml",
        use_view=False,
    ),
    variant(
        "sdk-view",
        "SDK View attribute_keys, baseline Collector config",
        "collector-baseline.yaml",
        use_view=True,
    ),
    variant(
        "collector-transform",
        "full attributes, aggregate_on_attributes in the Collector",
        "collector-transform.yaml",
        use_view=False,
    ),
)


def environment(variant: Variant) -> dict[str, str]:
    return stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
    )


def run_emitter(use_view: bool) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(EMITTER), json.dumps({"use_view": use_view})],
        cwd=stack.ROOT,
        env={**os.environ, "PYTHONPATH": str(stack.ROOT)},
        capture_output=True,
        text=True,
        timeout=EMITTER_TIMEOUT,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def readings(env: dict[str, str], socket_path: str) -> dict[str, Any]:
    return stack.raw_stats("collector", env, socket_path)


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


def wait_for_delivery(env: dict[str, str]) -> tuple[bool, int]:
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    delivered = 0
    while time.monotonic() < deadline:
        delivered = sum(debuglog.export_requests(stack.logs("sink", env), "metrics"))
        if delivered >= EXPECTED_REDUCED_IDENTITIES:
            return True, delivered
        time.sleep(POLL_SECONDS)
    return False, delivered


def quiesce(env: dict[str, str], socket_path: str, start: Mapping[str, Any]) -> dict:
    """Wait for sink delivery, then for collector RX to stop moving."""
    wait_for_delivery(env)
    last = readings(env, socket_path)
    flat = 0
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    while time.monotonic() < deadline and flat < QUIET_READINGS:
        time.sleep(POLL_SECONDS)
        current = readings(env, socket_path)
        same = current["rx_bytes"] == last["rx_bytes"]
        flat = flat + 1 if same else 0
        last = current
    return last


def prometheus_metrics() -> dict[str, Any]:
    wait_for_url(f"{stack.PROMETHEUS_URL}/-/ready")
    deadline = time.monotonic() + DELIVERY_TIMEOUT
    observations = None
    while time.monotonic() < deadline:
        observations = query_value(stack.PROMETHEUS_URL, f"sum({METRIC_COUNT})")
        if observations == OBSERVATIONS:
            break
        time.sleep(POLL_SECONDS)
    count_series = query_result(stack.PROMETHEUS_URL, METRIC_COUNT)
    bucket_series = query_result(stack.PROMETHEUS_URL, METRIC_BUCKET)
    inbound = query_result(stack.PROMETHEUS_URL, INBOUND)
    identities = len(count_series)
    family = identities + len(bucket_series)
    # each identity also has _sum; count query already has one per identity
    sum_series = query_result(
        stack.PROMETHEUS_URL,
        '{__name__=~"http_server_duration_milliseconds_sum", job="exported"}',
    )
    family = len(count_series) + len(sum_series) + len(bucket_series)
    user_after = any(USER_LABEL in item["metric"] for item in count_series)
    inbound_users = {
        item["metric"].get(USER_LABEL, "none")
        for item in inbound
        if item["metric"].get(USER_LABEL, "none") != "none"
    }
    inbound_none = sum(
        float(item["value"][1])
        for item in inbound
        if item["metric"].get(USER_LABEL, "none") == "none"
    )
    inbound_with_user = sum(
        float(item["value"][1])
        for item in inbound
        if item["metric"].get(USER_LABEL, "none") != "none"
    )
    p95 = query_value(
        stack.PROMETHEUS_URL,
        f"histogram_quantile(0.95, sum by (http_method, le) ({METRIC_BUCKET}))",
    )
    p95_by_method = {
        item["metric"].get("http_method", ""): round(float(item["value"][1]), 3)
        for item in query_result(
            stack.PROMETHEUS_URL,
            f"histogram_quantile(0.95, sum by (http_method, le) ({METRIC_BUCKET}))",
        )
        if item["value"][1] not in ("NaN", "+Inf", "-Inf")
    }
    return {
        "observations": None if observations is None else round(observations),
        "identity_series": identities,
        "family_series": family,
        "user_id_after": user_after,
        "inbound_user_values": len(inbound_users),
        "inbound_with_user_id": round(inbound_with_user),
        "inbound_without_user_id": round(inbound_none),
        "p95_by_method": p95_by_method,
        "p95_overall": None if p95 is None else round(p95, 3),
    }


def send(variant: Variant) -> dict[str, Any]:
    env = environment(variant)
    socket_path = stack.docker_socket()
    engine = stack.engine_version(socket_path)
    stack.compose("stop", "prometheus", env=env)
    prometheus_restored = False
    try:
        before = readings(env, socket_path)
        time.sleep(IDLE_SECONDS)
        idle_end = readings(env, socket_path)
        began = time.monotonic()
        emitted = run_emitter(bool(variant.options["use_view"]))
        after = quiesce(env, socket_path, idle_end)
        work_seconds = round(time.monotonic() - began, 3)
    finally:
        stack.compose("start", "prometheus", env=env)
        try:
            wait_for_url(f"{stack.PROMETHEUS_URL}/-/ready")
            prometheus_restored = True
        except TimeoutError:
            prometheus_restored = False
    idle = window(before, idle_end)
    work = window(idle_end, after)
    idle_rate = idle["cpu_ns"] / IDLE_SECONDS if IDLE_SECONDS else 0.0
    adjusted = work["cpu_ns"] - idle_rate * work_seconds
    background = (
        (idle["rx_bytes"] or 0) / IDLE_SECONDS * work_seconds if IDLE_SECONDS else 0.0
    )
    net = None if work["rx_bytes"] is None else round(work["rx_bytes"] - background)
    metrics = prometheus_metrics() if prometheus_restored else {}
    state = stack.container_state("collector", env)
    return {
        "engine": engine,
        "observations_sent": emitted["observations"],
        "use_view": emitted["use_view"],
        "flushed": emitted["flushed"],
        "exports": emitted["exports"],
        "sdk_request_bytes": emitted["request_bytes"],
        "sdk_wall_s": emitted["sdk_wall_s"],
        "sdk_cpu_s": emitted["sdk_cpu_s"],
        "collector_rx_bytes": net,
        "collector_cpu_ns": work["cpu_ns"],
        "adjusted_collector_cpu_ns": round(adjusted),
        "work_seconds": work_seconds,
        "prometheus_restored": prometheus_restored,
        "collector_running": state["running"],
        "collector_oom": state["oom_killed"],
        "collector_restarts": state["restarts"],
        "window_problems": idle["problems"] + work["problems"],
        **{f"metric_{key}": value for key, value in metrics.items()},
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    requests = debuglog.export_requests(sink_log, "metrics")
    return {
        "export_requests": len(requests),
        "delivered_datapoints": sum(requests),
        "largest_request": max(requests, default=0),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    counters = run["counters"]
    notes = list(sending.get("window_problems", []))
    if sending.get("observations_sent") != OBSERVATIONS:
        notes.append(f"emitter sent {sending.get('observations_sent')} observations")
    if not sending.get("flushed"):
        notes.append("the SDK force_flush returned false")
    if not sending.get("prometheus_restored"):
        notes.append("Prometheus did not come back")
    if sending.get("metric_observations") != OBSERVATIONS:
        notes.append(
            f"Prometheus counted {sending.get('metric_observations')} observations"
        )
    expected_identities = (
        EXPECTED_BASELINE_IDENTITIES
        if variant.name == "baseline"
        else EXPECTED_REDUCED_IDENTITIES
    )
    if sending.get("metric_identity_series") != expected_identities:
        notes.append(
            f"identity series were {sending.get('metric_identity_series')}, "
            f"expected {expected_identities}"
        )
    expected_family = expected_identities * FAMILY_PER_IDENTITY
    if sending.get("metric_family_series") != expected_family:
        notes.append(
            f"family series were {sending.get('metric_family_series')}, "
            f"expected {expected_family}"
        )
    if variant.name == "baseline" and not sending.get("metric_user_id_after"):
        notes.append("baseline lost user.id on the exported series")
    if variant.name != "baseline" and sending.get("metric_user_id_after"):
        notes.append(f"{variant.name} still exported user.id")
    if variant.name == "sdk-view":
        if sending.get("metric_inbound_user_values"):
            notes.append("sdk-view presented user.id to the Collector")
        if sending.get("metric_inbound_with_user_id"):
            notes.append("sdk-view inbound count saw user.id values")
    if (
        variant.name == "collector-transform"
        and sending.get("metric_inbound_user_values") != EXPECTED_BASELINE_IDENTITIES
    ):
        notes.append(
            "transform inbound user.id values were "
            f"{sending.get('metric_inbound_user_values')}"
        )
    if not sending.get("collector_running"):
        notes.append("the collector is not running")
    if sending.get("collector_oom"):
        notes.append("the collector was OOM-killed")
    if sending.get("collector_restarts"):
        notes.append(f"the collector restarted {sending['collector_restarts']} times")
    if measured.get("delivered_datapoints", 0) <= 0:
        notes.append("the sink logged no metric datapoints")
    collector = counters.get("collector", {}).get("metrics", {})
    for name in ("receiver_refused", "exporter_send_failed"):
        if collector.get(name):
            notes.append(f"the collector reported {name} {collector[name]:g}")
    return notes


QUESTION = Question(
    id="L4-02",
    title="Should the fix live in the SDK or in the platform?",
    level=4,
    theme="sdk, metrics",
    signals=("metrics",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=3,
    rotate_variants=True,
    columns=(
        Column("SDK request B", "sending.sdk_request_bytes"),
        Column("Collector RX B", "sending.collector_rx_bytes"),
        Column("SDK wall s", "sending.sdk_wall_s", digits=3),
        Column("SDK CPU s", "sending.sdk_cpu_s", digits=3),
        Column("Collector CPU ns", "sending.adjusted_collector_cpu_ns"),
        Column("Inbound user.id values", "sending.metric_inbound_user_values"),
        Column("Inbound with user.id", "sending.metric_inbound_with_user_id"),
        Column("Identity series", "sending.metric_identity_series"),
        Column("Family series", "sending.metric_family_series"),
        Column("Observations", "sending.metric_observations"),
        Column("p95 overall", "sending.metric_p95_overall", digits=3),
        Column("user.id after", "sending.metric_user_id_after", kind="text"),
        Column("Sink datapoints", "measured.delivered_datapoints"),
        Column("Exports", "sending.exports"),
    ),
    interpretation="""
Both reductions produced the same Prometheus answer: 20 identity series and
220 family series, 600 observations, and the same per-method p95. The
difference is what each side still knew after the cut.

The SDK View never showed `user.id` to the platform. The inbound count
connector saw 20 datapoints under the default `none`, and the SDK request
shrank from 63054 bytes to 6070, about one tenth. The Collector transform
paid the full 63054 byte payload first: its inbound count saw all 200
`user.id` values, then removed them before export. A fan-out that needs the
user, or a second pipeline that does, is still possible only on the
Collector path.

SDK wall time was not higher with the View. Emitting and flushing took
0.007 s with the View against 0.010 to 0.013 s without it, because the
exporter encoded 20 histogram points instead of 200. Collector CPU over the
window was in the same order for every variant on this small burst; the
clear cost signal is the bytes on the SDK-to-Collector hop, not the
nanoseconds inside either process.

What this does to an operation: put the View in the application when the
attribute must never leave the process and a release of that application is
acceptable. Keep the aggregating transform in the Collector when the
platform must still see the attribute, change the allow-list without an
application release, or fan the raw stream out before it is reduced. Deploy
time is not measured here; ownership and recoverability are.
""",
    does_not_say="""
Measured with `opentelemetry-sdk 1.45.0` and
`opentelemetry-exporter-otlp-proto-http 1.45.0` on Python 3.14, one Delta
histogram, 600 observations, and a single force-flush. Continuous scrapes,
multi-service fan-out, and the calendar time to ship an SDK change versus a
Collector config change are outside the run.

The transform config normalizes datapoint timestamps before
`aggregate_on_attributes`. Without that step the Prometheus exporter of
`otelcol-contrib 0.162.0` drops histogram points whose starts differ after
the attributes collapse. That interaction is part of the Collector path's
operational cost, not a claim about every backend.

Collector CPU on a burst this small is noisy across Docker Desktop on
Darwin arm64; treat the byte counts and the inbound count connector as the
stable evidence.
""",
)
