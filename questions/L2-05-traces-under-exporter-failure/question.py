"""L2-05: twenty traces whose spans straddle an outage, under three retry budgets."""

import re
import time
from collections import Counter
from collections.abc import Mapping
from typing import Any

from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import counters_by_exporter, query_value
from opentelemetry_experiments.core.runner import Column, Question, Variant

SERVICE = {"service.name": "l2-05"}
SINK_EXPORTER = "otlp_grpc/sink"

TRACES = 20
SPANS_PER_TRACE = 5
REQUESTS = TRACES * SPANS_PER_TRACE
INTERVAL = 0.4

STOP_BEFORE = 25
START_BEFORE = 75
QUIET_SECONDS = 35
DRAIN_TIMEOUT = 180.0

CONFIG = "./questions/L2-05-traces-under-exporter-failure/config"

DROPPED = re.compile(r"Dropping data\.")
RETRYING = re.compile(r"Will retry the request")


def variant(name: str, description: str, config: str, fault: bool) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=f"{CONFIG}/{config}",
        sink_config=stack.SINK_DETAILED_CONFIG,
        options={"fault": fault},
    )


VARIANTS = (
    variant(
        "sink-up",
        "the sink reachable throughout, default retry",
        "collector-retry-default.yaml",
        fault=False,
    ),
    variant(
        "retry-default",
        "the sink stopped for 20s of the 40s, retry at its defaults",
        "collector-retry-default.yaml",
        fault=True,
    ),
    variant(
        "retry-off",
        "the sink stopped for 20s of the 40s, retry_on_failure disabled",
        "collector-retry-off.yaml",
        fault=True,
    ),
    variant(
        "retry-10s",
        "the sink stopped for 20s of the 40s, max_elapsed_time 10s",
        "collector-retry-10s.yaml",
        fault=True,
    ),
)


def trace_plan() -> tuple[list[str], list[list[str]]]:
    """Twenty trace ids with five span ids each, generated once per run."""
    traces = [otlp.hex_id(16) for _ in range(TRACES)]
    spans = [[otlp.hex_id(8) for _ in range(SPANS_PER_TRACE)] for _ in range(TRACES)]
    return traces, spans


def one_span(trace_id: str, span_ids: list[str], depth: int) -> dict[str, Any]:
    """Depth 0 is the root; every later span is a child of the root."""
    moment = otlp.now_unix_nano()
    return otlp.trace_payload(
        [
            otlp.span(
                f"step-{depth}",
                trace_id=trace_id,
                span_id=span_ids[depth],
                parent_span_id=None if depth == 0 else span_ids[0],
                start_unix_nano=moment - 1_000_000,
                end_unix_nano=moment,
                span_attributes={"l2_05.depth": depth},
            )
        ],
        SERVICE,
    )


def arrived_at_sink(offset: float = 0.0) -> float | None:
    current = query_value(
        stack.PROMETHEUS_URL,
        'sum({__name__=~"otelcol_receiver_accepted_spans(_total)?", job="sink"})',
    )
    return None if current is None else offset + current


def wait_for_sink(target: float, timeout: float = 15.0) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        current = arrived_at_sink()
        if current is not None and current >= target:
            return current
        time.sleep(0.5)
    raise TimeoutError(f"the sink did not accept {target:g} spans after {timeout:g}s")


def wait_for_quiet(
    offset: float = 0.0,
    initial: float | None = None,
    recovery_started: float | None = None,
) -> tuple[float, float | None]:
    """Spans at the sink once the count stopped rising.

    A retried request can sit idle for `max_interval`, so a stalled count is
    only called final after 35 quiet seconds. Reaching all 100 spans ends the
    wait after two agreeing samples instead.
    """
    started = time.monotonic()
    previous = initial
    last_change = recovery_started
    repeats = 0
    while time.monotonic() - started < DRAIN_TIMEOUT:
        current = arrived_at_sink(offset)
        if current is not None and current != previous:
            last_change = time.monotonic()
        repeats = repeats + 1 if current is not None and current == previous else 0
        complete = current is not None and current >= REQUESTS
        if repeats >= (2 if complete else QUIET_SECONDS):
            drain_seconds = (
                round(last_change - recovery_started, 1)
                if last_change is not None and recovery_started is not None
                else None
            )
            return current, drain_seconds
        previous = current
        time.sleep(1.0)
    raise TimeoutError(
        f"the sink count did not settle after {DRAIN_TIMEOUT:g}s; "
        f"last sample was {previous}"
    )


def send(variant: Variant) -> dict[str, Any]:
    """Span `j` of trace `t` is request `20j + t`, so every trace straddles the gap."""
    environment = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
    )
    traces, spans = trace_plan()
    fault = variant.options["fault"]
    statuses = set()
    accepted_before_restart = None
    queue_at_recovery = None
    recovery_started = None
    for index in range(REQUESTS):
        if fault and index == STOP_BEFORE:
            accepted_before_restart = wait_for_sink(STOP_BEFORE)
            stack.stop_sink(environment)
        if fault and index == START_BEFORE:
            queue_at_recovery = query_value(
                stack.PROMETHEUS_URL,
                'max({__name__="otelcol_exporter_queue_size", job="collector"})',
            )
            recovery_started = time.monotonic()
            stack.start_sink(environment)
        trace = index % TRACES
        result = otlp.post_json(
            stack.COLLECTOR_HTTP_URL,
            "traces",
            one_span(traces[trace], spans[trace], index // TRACES),
        )
        statuses.add(result["http_status"])
        time.sleep(INTERVAL)
    spans_at_quiet, drain_seconds = wait_for_quiet(
        offset=accepted_before_restart or 0.0,
        initial=accepted_before_restart,
        recovery_started=recovery_started,
    )
    return {
        "http_status": max(statuses),
        "requests_sent": REQUESTS,
        "spans_sent": REQUESTS,
        "spans_during_outage": START_BEFORE - STOP_BEFORE if fault else 0,
        "sink_counter_offset": accepted_before_restart,
        "queue_at_recovery": queue_at_recovery,
        "recovered": "yes" if fault else "not applicable",
        "spans_at_quiet": spans_at_quiet,
        "drain_seconds": drain_seconds,
    }


def exporter_counter(role: str) -> float | None:
    name = f"otelcol_exporter_{role}_spans"
    return query_value(
        stack.PROMETHEUS_URL,
        f'sum({{__name__=~"{name}(_total)?", job="collector"}})',
    )


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    by_exporter = counters_by_exporter(stack.PROMETHEUS_URL, "collector", "traces")
    traces = debuglog.spans_by_trace(sink_log)
    sizes = [len(spans) for spans in traces.values()]
    size_counts = dict(sorted(Counter(sizes).items()))
    collector = stack.unprefixed(collector_log)
    return {
        "sent_to_sink": by_exporter.get(SINK_EXPORTER),
        "sink_accepted_total": sending["spans_at_quiet"],
        "send_failed": exporter_counter("send_failed"),
        "enqueue_failed": exporter_counter("enqueue_failed"),
        "queue_size_max": query_value(
            stack.PROMETHEUS_URL,
            'max(max_over_time({__name__="otelcol_exporter_queue_size",'
            ' job="collector"}[10m]))',
        ),
        "sink_up": query_value(stack.PROMETHEUS_URL, 'up{job="sink"}'),
        "spans_arrived": sum(sizes),
        "traces_seen": len(traces),
        "traces_complete": sizes.count(SPANS_PER_TRACE),
        "traces_incomplete": sum(1 for size in sizes if 0 < size < SPANS_PER_TRACE),
        "traces_missing": TRACES - len(traces),
        "trace_size_counts": size_counts,
        "trace_size_distribution": "; ".join(
            f"{size}:{count}" for size, count in size_counts.items()
        ),
        "roots_arrived": sum(
            1 for parent in debuglog.parents_by_span(sink_log).values() if parent == ""
        ),
        "largest_hole": SPANS_PER_TRACE - min(sizes, default=SPANS_PER_TRACE),
        "requests_at_sink": len(debuglog.export_requests(sink_log, "traces")),
        "drop_log_lines": len(DROPPED.findall(collector)),
        "retry_log_lines": len(RETRYING.findall(collector)),
        "queue_at_recovery": sending["queue_at_recovery"],
        "drain_seconds": sending["drain_seconds"],
        "recovered": sending["recovered"],
    }


QUESTION = Question(
    id="L2-05",
    title="What does a trace look like when the exporter failed halfway through it?",
    level=2,
    theme="traces",
    signals=("traces",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    columns=(
        Column("HTTP status", "sending.http_status"),
        Column("Spans sent", "sending.spans_sent"),
        Column("Accepted", "counters.collector.traces.receiver_accepted"),
        Column("Sent to the sink", "measured.sent_to_sink"),
        Column("Send failed", "measured.send_failed"),
        Column("Enqueue failed", "measured.enqueue_failed"),
        Column("Queue size max", "measured.queue_size_max"),
        Column("Queue at recovery", "measured.queue_at_recovery"),
        Column("Spans arrived", "measured.spans_arrived"),
        Column("Sink accepted", "measured.sink_accepted_total"),
        Column("Complete traces", "measured.traces_complete"),
        Column("Incomplete traces", "measured.traces_incomplete"),
        Column("Missing traces", "measured.traces_missing"),
        Column(
            "Trace size distribution",
            "measured.trace_size_distribution",
            kind="text",
        ),
        Column("Roots arrived", "measured.roots_arrived"),
        Column("Largest hole", "measured.largest_hole"),
        Column("Seconds to drain", "measured.drain_seconds", digits=1),
        Column("Requests at the sink", "measured.requests_at_sink"),
        Column("Retry lines", "measured.retry_log_lines"),
        Column("Drop lines", "measured.drop_log_lines"),
        Column("Sink target up", "measured.sink_up"),
        Column("Error lines", "log.error_lines"),
        Column("Recovered", "measured.recovered", kind="text"),
    ),
    interpretation="""
With retry disabled, a 20 second outage did not lose traces. It corrupted all
of them. Every one of the twenty traces arrived at the sink, and not one of them
was complete. Across the three repetitions, 11 to 16 traces held 2 of their 5
spans and 4 to 9 held 3. `trace_size_counts` now carries that distribution in
`results.json`, instead of leaving it to a manual reading of the sink log.

All twenty root spans arrived in every variant, including the two lossy ones.
The root was sent before the outage began, so all twenty trace ids exist at the
destination while the missing children are visible only as an absence.

The default retry absorbed the outage completely. `retry-default` delivered 100
spans and 20 complete traces in all three repetitions, with 0 error lines and 0
drop lines. The queue held 48 to 50 requests immediately before recovery,
peaked at 55 to 61, and the last sink-counter change came 20.4 to 26.4 seconds
after restart. The cumulative sink counter and the unsampled sink log both read
100, which verifies the two evidence paths after accounting for the counter
reset at restart.

Shortening the budget recovered 78 to 80 spans, but only 0 or 1 complete trace.
At recovery the queue gauge read 30 to 32 requests, the final loss was 20 to 22
spans, and the last sink-counter change came 10.3 to 11.3 seconds after restart.
Those observations are consistent with queued requests surviving long enough
to be retried, but this run does not correlate a queue slot with a span and does
not establish which queue positions survived.

The counters measured here do not know what a trace is.
`otelcol_exporter_send_failed_spans` counted 51 to 56 spans under `retry-off`,
which cannot distinguish 20 damaged traces from a smaller number of missing
ones. `queue_size` counts requests. `enqueue_failed` was never published,
because the queue peaked at 61 of its 1000 request capacity.

The early signal from L2-01 disappears in the worst case. `queue_size_max` reads
0 under `retry-off`: with retry disabled the request fails on its first attempt
and never dwells in the queue, so the gauge stays flat while 51 to 56 spans are
discarded. Queue depth warns about delivery that is waiting, not about delivery
already abandoned.

What remains is the log, and it is sampled. Each dropped request produced
`Exporting failed. Dropping data. Try enabling retry_on_failure config option to
retry on retryable errors.` at `error` with `"dropped_items": 1`, one span per
line. `retry-off` recorded 21 to 26 of those lines for 51 to 56 dropped spans,
so the text undercounts under the Collector's default sampler. The counter is
the number to alert on; the log line is where the reason is.

What this does to a dashboard: alert on `send_failed_spans` for the loss and on
destination availability for the cause, and accept that neither says a trace is
incomplete. Detecting that needs the backend to compare spans against their
parents, because the Collector never had the whole trace in one place.
""",
    does_not_say="""
Measured on `otelcol-contrib 0.162.0` with 20 traces of 5 spans, one span per
OTLP/HTTP request 0.4 seconds apart over 40 seconds, a single 20 second outage
starting at request 25, `timeout: 5s`, the default in-memory queue with
`num_consumers: 10`, no batch processor, and no tail sampling. Only
`retry_on_failure` varies.

The span ordering is chosen to produce holes. Span `j` of trace `t` is request
`20j + t`, so every trace necessarily straddles the outage. A real service
usually finishes a trace in milliseconds, where the same outage would lose whole
traces instead and `Missing traces` would carry the damage. This run shows what
long-lived traces do, not what the average trace does.

`retry-10s` also sets `max_interval: 5s`, because the Collector refuses
`max_elapsed_time` below `max_interval` and the default ceiling is 30 seconds.
Its numbers are therefore a shorter budget and a shorter backoff together, and
this run cannot separate the two.

The split between delivered and dropped spans is not deterministic.
`retry-off` delivered 44 to 49 of 100 against 50 sent during the outage,
because requests in flight when the sink stopped, and the first requests after
it started, land on either side depending on timing within a 0.4 second step.

`queue_at_recovery` is the latest Prometheus sample immediately before restart,
so its resolution is the one-second scrape interval. It proves the observed
queue depth at that boundary, not which spans occupied those requests.

Log line counts are floors. The Collector keeps its default sampler, so
`retry_log_lines` and `drop_log_lines` undercount events; `send_failed` and the
trace figures from the sink's unsampled log carry the result.

Nothing here tests a batch processor in front of the exporter, which would put
several spans of several traces in one request and change which spans share a
fate. Nothing tests a persistent queue, a Collector restart during the outage,
or a backend that rejects rather than refuses. The sink was always recovered,
so the behaviour after the retry budget is exhausted against a destination that
never returns is L2-07, not this.
""",
)
