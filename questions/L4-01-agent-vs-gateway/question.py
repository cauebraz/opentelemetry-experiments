"""L4-01: agent against gateway, and which counters double-count."""

import time
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.debuglog import export_requests
from opentelemetry_experiments.core.prometheus import (
    query_value,
    self_metric_totals,
    wait_for_url,
)
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = "./questions/L4-01-agent-vs-gateway/config"
AGENT_CONFIG = f"{DIRECTORY}/agent.yaml"
GATEWAY_CONFIG = f"{DIRECTORY}/gateway.yaml"
PROMETHEUS_CONFIG = f"{DIRECTORY}/prometheus.yaml"
SERVICE = {"service.name": "l4-01"}
SPANS = 1000
HOLD_SECONDS = 8.0
DRAIN_TIMEOUT = 90.0


def payload() -> dict[str, Any]:
    start = otlp.now_unix_nano()
    spans = [
        otlp.span(
            name=f"span-{index:04d}",
            trace_id=f"{index + 1:032x}",
            span_id=f"{index + 1:016x}",
            start_unix_nano=start + index,
            end_unix_nano=start + index + 1_000_000,
            span_attributes={"sequence": index},
        )
        for index in range(SPANS)
    ]
    return otlp.trace_payload(spans, SERVICE)


def variant(name: str, description: str, *, fault: str | None) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=AGENT_CONFIG,
        sink_config=stack.SINK_CONFIG,
        prometheus_config=PROMETHEUS_CONFIG,
        gateway_config=GATEWAY_CONFIG,
        options={"fault": fault},
    )


VARIANTS = (
    variant("healthy", "all three layers reachable", fault=None),
    variant(
        "gateway-paused",
        "gateway paused between burst and recovery",
        fault="gateway",
    ),
    variant(
        "sink-paused",
        "sink paused between burst and recovery",
        fault="sink",
    ),
)


def environment() -> dict[str, str]:
    return stack.environment(
        collector_config=AGENT_CONFIG,
        sink_config=stack.SINK_CONFIG,
        prometheus_config=PROMETHEUS_CONFIG,
        gateway_config=GATEWAY_CONFIG,
    )


def role_counter(job: str, name: str) -> float | None:
    return query_value(
        stack.PROMETHEUS_URL,
        f'sum({{__name__=~"{name}(_total)?", job="{job}"}})',
    )


def snapshot_layers() -> dict[str, float | None]:
    fields = (
        ("agent_accepted", "collector", "otelcol_receiver_accepted_spans"),
        ("agent_sent", "collector", "otelcol_exporter_sent_spans"),
        ("agent_send_failed", "collector", "otelcol_exporter_send_failed_spans"),
        ("agent_queue", "collector", "otelcol_exporter_queue_size"),
        ("gateway_accepted", "gateway", "otelcol_receiver_accepted_spans"),
        ("gateway_sent", "gateway", "otelcol_exporter_sent_spans"),
        ("gateway_send_failed", "gateway", "otelcol_exporter_send_failed_spans"),
        ("gateway_queue", "gateway", "otelcol_exporter_queue_size"),
        ("sink_accepted", "sink", "otelcol_receiver_accepted_spans"),
    )
    return {key: role_counter(job, metric) for key, job, metric in fields}


def self_queue(url: str) -> float | None:
    totals = self_metric_totals(url)
    return totals.get("otelcol_exporter_queue_size")


def wait_for_sink(target: float = float(SPANS)) -> float | None:
    started = time.monotonic()
    previous: float | None = None
    repeats = 0
    while time.monotonic() - started < DRAIN_TIMEOUT:
        current = role_counter("sink", "otelcol_receiver_accepted_spans")
        settled = current is not None and current >= target and current == previous
        repeats = repeats + 1 if settled else 0
        if repeats >= 2:
            return round(time.monotonic() - started, 2)
        previous = current
        time.sleep(1.0)
    return None


def wait_queues_empty(env: dict[str, str]) -> bool:
    deadline = time.monotonic() + DRAIN_TIMEOUT
    while time.monotonic() < deadline:
        agent_q = self_queue(stack.COLLECTOR_SELF_METRICS_URL)
        gateway_q = self_queue(stack.GATEWAY_SELF_METRICS_URL)
        if agent_q in (0, 0.0, None) and gateway_q in (0, 0.0, None):
            # None can mean the gauge was never published; confirm via Prometheus.
            layers = snapshot_layers()
            if (layers.get("agent_queue") or 0) == 0 and (
                layers.get("gateway_queue") or 0
            ) == 0:
                return True
        time.sleep(1.0)
    return False


def send(variant: Variant) -> dict[str, Any]:
    env = environment()
    fault = variant.options.get("fault")
    if fault == "gateway":
        stack.pause_gateway(env)
    elif fault == "sink":
        stack.pause_sink(env)

    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "traces", payload())
    time.sleep(2.0)
    during_agent_queue = self_queue(stack.COLLECTOR_SELF_METRICS_URL)
    during_gateway_queue = None
    if fault != "gateway":
        during_gateway_queue = self_queue(stack.GATEWAY_SELF_METRICS_URL)
    during_agent_send_failed = role_counter(
        "collector", "otelcol_exporter_send_failed_spans"
    )
    during_gateway_accepted = (
        None
        if fault == "gateway"
        else role_counter("gateway", "otelcol_receiver_accepted_spans")
    )
    during_sink_accepted = role_counter("sink", "otelcol_receiver_accepted_spans")

    time.sleep(HOLD_SECONDS)

    restored = True
    drain_s: float | None = None
    if fault == "gateway":
        stack.unpause_gateway(env)
        try:
            wait_for_url(stack.GATEWAY_HEALTH_URL)
        except TimeoutError:
            restored = False
    elif fault == "sink":
        stack.unpause_sink(env)
        try:
            wait_for_url(stack.SINK_HEALTH_URL)
        except TimeoutError:
            restored = False

    if restored:
        drain_s = wait_for_sink()
        queues_empty = wait_queues_empty(env)
    else:
        queues_empty = False

    after = snapshot_layers()
    agent_state = stack.container_state("collector", env)
    gateway_state = stack.container_state("gateway", env)
    sink_state = stack.container_state("sink", env)
    agent_sent = after.get("agent_sent")
    gateway_accepted = after.get("gateway_accepted")
    double = None
    if agent_sent is not None and gateway_accepted is not None:
        double = round(agent_sent + gateway_accepted)

    return {
        "http_status": result["http_status"],
        "spans_sent": SPANS,
        "fault": fault or "none",
        "hold_s": HOLD_SECONDS,
        "restored": restored,
        "drain_s": drain_s,
        "queues_empty": queues_empty,
        "during_agent_queue": during_agent_queue,
        "during_gateway_queue": during_gateway_queue,
        "during_agent_send_failed": during_agent_send_failed,
        "during_gateway_accepted": during_gateway_accepted,
        "during_sink_accepted": during_sink_accepted,
        "agent_accepted": after.get("agent_accepted"),
        "agent_sent": agent_sent,
        "agent_send_failed": after.get("agent_send_failed"),
        "agent_queue": after.get("agent_queue"),
        "gateway_accepted": gateway_accepted,
        "gateway_sent": after.get("gateway_sent"),
        "gateway_send_failed": after.get("gateway_send_failed"),
        "gateway_queue": after.get("gateway_queue"),
        "sink_accepted": after.get("sink_accepted"),
        "double_count_sum": double,
        "agent_running": agent_state["running"],
        "gateway_running": gateway_state["running"],
        "sink_running": sink_state["running"],
        "agent_oom": agent_state["oom_killed"],
        "gateway_oom": gateway_state["oom_killed"],
        "sink_oom": sink_state["oom_killed"],
        "agent_restarts": agent_state["restarts"],
        "gateway_restarts": gateway_state["restarts"],
        "sink_restarts": sink_state["restarts"],
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    sink_items = sum(export_requests(sink_log, "traces"))
    return {
        "sink_log_spans": sink_items,
        "double_count_sum": sending.get("double_count_sum"),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes: list[str] = []
    if sending.get("http_status") != 200:
        notes.append(f"agent returned {sending.get('http_status')}")
    for role in ("agent", "gateway", "sink"):
        if not sending.get(f"{role}_running"):
            notes.append(f"{role} is not running")
        if sending.get(f"{role}_oom"):
            notes.append(f"{role} was OOM-killed")
        if sending.get(f"{role}_restarts"):
            notes.append(f"{role} restarted {sending.get(f'{role}_restarts')} times")
    if variant.options.get("fault") and not sending.get("restored"):
        notes.append("the paused layer did not come back")
    if sending.get("sink_accepted") != SPANS:
        notes.append(f"sink accepted {sending.get('sink_accepted')} not {SPANS}")
    # basic debug log counts export requests' item totals
    if measured.get("sink_log_spans") not in (None, SPANS):
        notes.append(f"sink log counted {measured.get('sink_log_spans')} spans")
    if not sending.get("queues_empty"):
        notes.append("a queue was still non-empty after drain")
    if (sending.get("agent_queue") or 0) != 0 or (
        sending.get("gateway_queue") or 0
    ) != 0:
        notes.append("residual queue after recovery")
    if variant.name == "healthy":
        if sending.get("double_count_sum") != SPANS * 2:
            notes.append(
                f"healthy double-count sum was {sending.get('double_count_sum')}"
            )
        if sending.get("agent_accepted") != SPANS:
            notes.append(f"agent accepted {sending.get('agent_accepted')}")
        if sending.get("gateway_accepted") != SPANS:
            notes.append(f"gateway accepted {sending.get('gateway_accepted')}")
    return notes


QUESTION = Question(
    id="L4-01",
    title=(
        "What double counts, and what goes invisible, "
        "in an agent plus gateway topology?"
    ),
    level=4,
    theme="collector",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=3,
    rotate_variants=True,
    columns=(
        Column("HTTP", "sending.http_status"),
        Column("Agent accepted", "sending.agent_accepted"),
        Column("Agent sent", "sending.agent_sent"),
        Column("Agent send_failed", "sending.agent_send_failed"),
        Column("Gateway accepted", "sending.gateway_accepted"),
        Column("Gateway sent", "sending.gateway_sent"),
        Column("Sink accepted", "sending.sink_accepted"),
        Column("Double-count sum", "sending.double_count_sum"),
        Column("During agent queue", "sending.during_agent_queue"),
        Column("During gateway queue", "sending.during_gateway_queue"),
        Column("Drain s", "sending.drain_s", digits=1),
        Column("Restored", "sending.restored", kind="text"),
        Column("Sink log spans", "measured.sink_log_spans"),
    ),
    interpretation="""
Every span crossed two collectors before the sink. On the healthy path the
agent accepted 1000, sent 1000, the gateway accepted 1000 and sent 1000, and
the sink accepted 1000. Adding agent sent to gateway accepted produced 2000:
the same 1000 items counted once per hop. A dashboard that sums those two
jobs double-counts delivery.

Pausing the gateway left the sender at HTTP 200 and raised the agent queue
to 1 while `send_failed` stayed empty; after unpause the sink reached 1000
in about 3 s. Pausing the sink left the agent queue at 0 and raised the
gateway queue to 1; the agent layer alone could not show the sink outage.
Queues drained to empty in every recovery. The number that answers "did it
land" is the sink's accepted counter, not a sum across layers.
""",
    does_not_say="""
Measured with one agent, one gateway and one sink on
`otelcol-contrib 0.162.0`, 1000 spans, batch size 256, and the same queue
and retry settings on both hops. Load-balanced gateways, tail sampling,
TLS between tiers, and pause durations other than 8 s are outside the run.
Docker Desktop on Darwin arm64 is the host.
""",
)
