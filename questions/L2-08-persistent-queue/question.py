"""L2-08: what a Collector restart does to a queue the sink never drained."""

import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.debuglog import log_bodies
from opentelemetry_experiments.core.prometheus import (
    self_metric_totals,
    wait_for_url,
)
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l2-08"}
REQUESTS = 10
RECORDS_PER_REQUEST = 10
RECORDS = REQUESTS * RECORDS_PER_REQUEST

CONTEXT = "./questions/L2-08-persistent-queue"
IMAGE = "opentelemetry-experiments-l2-08:0.162.0"
MEMORY = f"{CONTEXT}/config/collector-queue-memory.yaml"
VOLUME = f"{CONTEXT}/config/collector-queue-volume.yaml"

ACCEPTED = "otelcol_receiver_accepted_log_records"
QUEUE_SIZE = "otelcol_exporter_queue_size"

POLL_SECONDS = 0.5
QUEUE_TIMEOUT = 30.0
AFTER_RESTART_WINDOW = 5.0
DELIVERY_TIMEOUT = 75.0
DELIVERY_QUIET = 10.0
NOTHING_ARRIVED_WAIT = 45.0

DROPPED_ITEMS = re.compile(r'"dropped_items":\s*(\d+)')


def variant(name: str, description: str, config: str, fault: str) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=config,
        sink_config=stack.SINK_DETAILED_CONFIG,
        collector_build_context=CONTEXT,
        collector_dockerfile="Dockerfile",
        collector_image=IMAGE,
        expects_acceptance=False,
        expects_telemetry=False,
        options={"fault": fault},
    )


VARIANTS = (
    variant(
        "memory-sigterm",
        "in-memory queue, Collector stopped with SIGTERM and started again",
        MEMORY,
        "sigterm",
    ),
    variant(
        "memory-sigkill",
        "in-memory queue, Collector killed with SIGKILL and started again",
        MEMORY,
        "sigkill",
    ),
    variant(
        "volume-sigterm",
        "file_storage queue on a volume, Collector stopped with SIGTERM",
        VOLUME,
        "sigterm",
    ),
    variant(
        "volume-sigkill",
        "file_storage queue on a volume, Collector killed with SIGKILL",
        VOLUME,
        "sigkill",
    ),
    variant(
        "volume-recreated",
        "file_storage queue on a volume, Collector killed with SIGKILL and "
        "recreated with a new volume",
        VOLUME,
        "recreate",
    ),
)


def record_bodies(request: int) -> list[str]:
    return [f"r{request:02d}-i{item:02d}" for item in range(RECORDS_PER_REQUEST)]


def request_payload(request: int) -> dict[str, Any]:
    moment = otlp.now_unix_nano()
    records = [
        otlp.log_record(body=body, time_unix_nano=moment)
        for body in record_bodies(request)
    ]
    return otlp.log_payload(records, SERVICE)


def metric(totals: Mapping[str, float], name: str) -> float | None:
    for key in (name, f"{name}_total"):
        if key in totals:
            return totals[key]
    return None


def read(url: str, name: str) -> float | None:
    try:
        return metric(self_metric_totals(url), name)
    except OSError:
        return None


def wait_for(
    probe: Callable[[], float | None],
    target: float,
    timeout: float,
) -> float | None:
    deadline = time.monotonic() + timeout
    value = probe()
    while time.monotonic() < deadline:
        value = probe()
        if value is not None and value >= target:
            return value
        time.sleep(POLL_SECONDS)
    return value


def highest(probe: Callable[[], float | None], seconds: float) -> float | None:
    deadline = time.monotonic() + seconds
    values = []
    while time.monotonic() < deadline:
        value = probe()
        if value is not None:
            values.append(value)
        time.sleep(POLL_SECONDS)
    return max(values) if values else None


def wait_for_delivery() -> dict[str, Any]:
    """The sink's accepted count once it stops moving, and when it last moved."""
    started = time.monotonic()
    last_value = read(stack.SINK_SELF_METRICS_URL, ACCEPTED) or 0.0
    last_change = None
    while time.monotonic() - started < DELIVERY_TIMEOUT:
        value = read(stack.SINK_SELF_METRICS_URL, ACCEPTED) or 0.0
        now = time.monotonic() - started
        if value != last_value:
            last_value, last_change = value, now
        if last_change is not None and now - last_change >= DELIVERY_QUIET:
            break
        if last_change is None and now >= NOTHING_ARRIVED_WAIT:
            break
        time.sleep(POLL_SECONDS)
    return {
        "delivered": last_value,
        "seconds_to_delivery": None if last_change is None else round(last_change, 1),
    }


def end_collector(fault: str, env: dict[str, str]) -> float:
    began = time.monotonic()
    if fault in ("sigkill", "recreate"):
        stack.compose("kill", "-s", "SIGKILL", "collector", env=env)
    else:
        stack.compose("stop", "collector", env=env)
    return round(time.monotonic() - began, 2)


def restart_collector(fault: str, env: dict[str, str]) -> None:
    if fault == "recreate":
        stack.compose(
            "up",
            "--detach",
            "--no-deps",
            "--force-recreate",
            "--renew-anon-volumes",
            "collector",
            env=env,
        )
    else:
        stack.compose("start", "collector", env=env)


def expected_starts(variant: Variant) -> int:
    return 1 if variant.options["fault"] == "recreate" else 2


def send(variant: Variant) -> dict[str, Any]:
    env = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
        collector_build_context=variant.collector_build_context,
        collector_dockerfile=variant.collector_dockerfile,
        collector_image=variant.collector_image,
    )
    stack.stop_sink(env)
    statuses = [
        otlp.post_json(stack.COLLECTOR_HTTP_URL, "logs", request_payload(index))[
            "http_status"
        ]
        for index in range(REQUESTS)
    ]
    accepted = wait_for(
        lambda: read(stack.COLLECTOR_SELF_METRICS_URL, ACCEPTED),
        RECORDS,
        QUEUE_TIMEOUT,
    )
    queued = wait_for(
        lambda: read(stack.COLLECTOR_SELF_METRICS_URL, QUEUE_SIZE),
        REQUESTS,
        QUEUE_TIMEOUT,
    )
    stop_seconds = end_collector(variant.options["fault"], env)
    restart_collector(variant.options["fault"], env)
    wait_for_url(stack.COLLECTOR_HEALTH_URL)
    after_restart = highest(
        lambda: read(stack.COLLECTOR_SELF_METRICS_URL, QUEUE_SIZE),
        AFTER_RESTART_WINDOW,
    )
    stack.start_sink(env)
    wait_for_url(stack.SINK_HEALTH_URL)
    return {
        "http_status": statuses[0] if len(set(statuses)) == 1 else "mixed",
        "requests_sent": len(statuses),
        "records_sent": RECORDS,
        "accepted_before_fault": accepted,
        "queue_before_fault": queued,
        "stop_seconds": stop_seconds,
        "queue_after_restart": after_restart,
    } | wait_for_delivery()


def duplicates(bodies: Sequence[str]) -> int:
    return sum(count - 1 for count in Counter(bodies).values() if count > 1)


def unknown(bodies: Sequence[str]) -> int:
    sent = {body for index in range(REQUESTS) for body in record_bodies(index)}
    return sum(1 for body in bodies if body not in sent)


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    bodies = log_bodies(sink_log)
    drop_lines = [
        line for line in collector_log.splitlines() if "Dropping data." in line
    ]
    return {
        "records_printed": len(bodies),
        "unique_records": len(set(bodies)),
        "duplicate_records": duplicates(bodies),
        "unknown_records": unknown(bodies),
        "drop_log_lines": len(drop_lines),
        "dropped_items": sum(
            int(match.group(1))
            for line in drop_lines
            for match in DROPPED_ITEMS.finditer(line)
        ),
        "starts_logged": collector_log.count("Everything is ready."),
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    """The fault has to land on a full queue the sink never saw."""
    sending = run["sending"]
    measured = run["measured"]
    notes = []
    if sending["accepted_before_fault"] != RECORDS:
        notes.append(
            f"the collector accepted {sending['accepted_before_fault']} records "
            f"before the fault, not {RECORDS}"
        )
    if sending["queue_before_fault"] != REQUESTS:
        notes.append(
            f"the queue held {sending['queue_before_fault']} requests before the "
            f"fault, not {REQUESTS}"
        )
    if measured["starts_logged"] != expected_starts(variant):
        notes.append(
            f"the collector log shows {measured['starts_logged']} starts, "
            f"not {expected_starts(variant)}"
        )
    if measured["records_printed"] != sending["delivered"]:
        notes.append(
            f"the sink printed {measured['records_printed']} records and counted "
            f"{sending['delivered']}"
        )
    if measured["unknown_records"]:
        notes.append(f"the sink printed {measured['unknown_records']} unknown records")
    return notes


QUESTION = Question(
    id="L2-08",
    title="What survives a Collector restart while the backend is down?",
    level=2,
    theme="collector",
    signals=("logs",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    columns=(
        Column("HTTP status", "sending.http_status", kind="text"),
        Column("Accepted before", "sending.accepted_before_fault"),
        Column("Queue before", "sending.queue_before_fault"),
        Column("Stop seconds", "sending.stop_seconds", digits=2),
        Column("Queue after restart", "sending.queue_after_restart"),
        Column("Delivered", "sending.delivered"),
        Column("Unique", "measured.unique_records"),
        Column("Duplicates", "measured.duplicate_records"),
        Column("Seconds to delivery", "sending.seconds_to_delivery", digits=1),
        Column("Drop log lines", "measured.drop_log_lines"),
        Column("Dropped items logged", "measured.dropped_items"),
        Column("Accepted after restart", "counters.collector.logs.receiver_accepted"),
        Column("Error lines", "log.error_lines"),
    ),
    interpretation="""
In every variant the sender received HTTP 200 ten times, the Collector counted
100 accepted records and the queue held all 10 requests before the fault. What
happened next depended only on where the queue lived and on whether a new
volume came with the restart.

The memory queue lost all 100 records under both signals. Under SIGTERM the
shutdown wrote 10 `error` lines, `Exporting failed. Dropping data.` with
`dropped_items: 10` each and `interrupted due to shutdown` in the error field,
so the loss is in the log. Under SIGKILL the log has no error line at all, and
the restarted Collector published no `accepted`, `sent` or `send_failed` series
for logs: 100 acknowledged records left no trace in its telemetry.

The persistent queue on a volume delivered 100 of 100 under both signals, with
0 duplicates. The restarted Collector read 10 requests in
`otelcol_exporter_queue_size` before the sink came back, and its log carried
`Moved items for dispatching back to queue` with `numberOfItems: 10`. Delivery
finished 10.1 to 12.1 seconds after the sink started. The restarted process
reports 100 `sent` and no `accepted` series, so sent without accepted is what
redispatch from disk looks like in the self-metrics.

The SIGTERM shutdown of the persistent queue wrote the same 10 `Dropping data.`
error lines, totalling `dropped_items: 100`, as the memory queue, and then
delivered all 100. A log alert on that line fires on a loss that did not
happen, while the memory SIGKILL, the run that did lose 100 records, writes
nothing for it to match.

A new volume with the same persistent config behaved as the memory queue under
SIGKILL: queue 0 after the restart, 0 delivered, 0 error lines. The config is
not the evidence; the storage's lifetime is.

For a dashboard, `otelcol_exporter_queue_size` above zero just after a start
is the sign that a persistent queue recovered something. For an alert, neither
the restarted Collector's counters nor its log can report data lost with a
memory queue or a new volume; only the destination's received count, compared
with what senders were acknowledged, can.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with `file_storage` at its defaults
(`fsync: false`), 10 requests of 10 log records, an OTLP gRPC destination
stopped for the whole fault and `docker compose stop` with Docker's 10 second
grace. A shutdown that outlasts the grace, a host crash, which is the case
`fsync` exists for, and a full or slow disk were not tested.

The sink was down from before the first request until after the restart, so no
request was in flight to a live destination when the process ended. Duplicates
need a request the destination received but whose result the Collector never
recorded; this setup cannot produce one, and 0 duplicates here says nothing
about that window.

`volume-recreated` stands in for storage that does not outlive the container,
such as an `emptyDir` on a rescheduled pod; it was not run on Kubernetes. The
first session's `tmpfs-sigkill` runs measured a Docker mount rule for images
without `/tmp`, recorded in `QUESTION.md`, and are not in this table.
""",
)
