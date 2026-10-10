"""L4-04: a one-attribute processor built into a custom Collector."""

import json
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opentelemetry_experiments.core import otlp, stack
from opentelemetry_experiments.core.debuglog import export_requests, log_attributes
from opentelemetry_experiments.core.prometheus import self_metric_totals
from opentelemetry_experiments.core.runner import Column, Question, Variant

DIRECTORY = Path("questions/L4-04-custom-processor-with-ocb")
CONTEXT = f"./{DIRECTORY.as_posix()}"
CUSTOM_IMAGE = "opentelemetry-experiments-l4-04:0.162.0"
PASSTHROUGH = f"{CONTEXT}/config/collector-passthrough.yaml"
CUSTOM = f"{CONTEXT}/config/cases/collector-custom.yaml"
BUILD_META = DIRECTORY / "build-meta.json"
LOGS = 1000
STAMP = "experiment.custom_processor"
READY = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z).*Everything is ready"
)
STARTED = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")


def _unix(timestamp: str) -> float:
    whole, _, fraction = timestamp.removesuffix("Z").partition(".")
    seconds = time.mktime(time.strptime(whole, "%Y-%m-%dT%H:%M:%S"))
    return seconds + (float(f"0.{fraction}") if fraction else 0.0)


def payload() -> dict[str, Any]:
    start = otlp.now_unix_nano()
    records = [
        otlp.log_record(
            body=f"record-{index:04d}",
            time_unix_nano=start + index,
            record_attributes={"sequence": index, "keep": "yes"},
        )
        for index in range(LOGS)
    ]
    return otlp.log_payload(records, {"service.name": "l4-04"})


def variant(
    name: str,
    description: str,
    *,
    custom: bool,
    processor: bool,
) -> Variant:
    return Variant(
        name=name,
        description=description,
        collector_config=CUSTOM if processor else PASSTHROUGH,
        sink_config=stack.SINK_DETAILED_CONFIG,
        collector_build_context=CONTEXT if custom else ".",
        collector_dockerfile="Dockerfile" if custom else "Dockerfile.collector",
        collector_image=CUSTOM_IMAGE if custom else "",
        options={
            "distribution": "custom" if custom else "stock",
            "processor": processor,
        },
    )


VARIANTS = (
    variant(
        "stock-pass-through",
        "otelcol-contrib, batch only",
        custom=False,
        processor=False,
    ),
    variant(
        "custom-pass-through",
        "custom distribution, batch only",
        custom=True,
        processor=False,
    ),
    variant(
        "custom-processor",
        "custom distribution with the experiment processor",
        custom=True,
        processor=True,
    ),
)


def build_meta() -> dict[str, Any]:
    path = stack.ROOT / BUILD_META
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def validate_distribution(config: str, *, custom: bool) -> int:
    env = stack.environment(
        collector_config=config,
        collector_build_context=CONTEXT if custom else ".",
        collector_dockerfile="Dockerfile" if custom else "Dockerfile.collector",
        collector_image=CUSTOM_IMAGE if custom else "",
    )
    result = stack.compose(
        "run",
        "--rm",
        "--no-deps",
        "collector",
        "validate",
        "--config=/etc/otelcol-contrib/config.yaml",
        env=env,
        check=False,
    )
    return result.returncode


def startup_seconds(env: dict[str, str]) -> float | None:
    text = stack.compose("logs", "--no-color", "collector", env=env).stdout
    first = STARTED.search(text)
    ready = READY.search(text)
    if first is None or ready is None:
        return None
    return round(_unix(ready.group("ts")) - _unix(first.group("ts")), 3)


def memory_bytes(env: dict[str, str]) -> int | None:
    sample = stack.container_stats("collector", env)
    token = str(sample.get("MemUsage", "")).split(" / ")[0].strip()
    match = re.fullmatch(r"(?P<value>[0-9.]+)(?P<unit>[KMG]i?B)", token)
    if match is None:
        return None
    scale = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
    }
    return round(float(match.group("value")) * scale[match.group("unit")])


def sink_accepted() -> float | None:
    """Read the sink's own :8888 text. A Prometheus query in the same second
    often still has no sample, which made the first session look like a loss.
    """
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        totals = self_metric_totals(stack.SINK_SELF_METRICS_URL)
        for key in (
            "otelcol_receiver_accepted_log_records",
            "otelcol_receiver_accepted_log_records_total",
        ):
            if key in totals:
                return totals[key]
        time.sleep(0.5)
    return None


def send(variant: Variant) -> dict[str, Any]:
    env = stack.environment(
        collector_config=variant.collector_config,
        sink_config=variant.sink_config,
        collector_build_context=variant.collector_build_context,
        collector_dockerfile=variant.collector_dockerfile,
        collector_image=variant.collector_image,
    )
    meta = build_meta()
    before = stack.raw_stats("collector", env)
    idle_rss = memory_bytes(env)
    heap = self_metric_totals(stack.COLLECTOR_SELF_METRICS_URL)
    began = time.monotonic()
    result = otlp.post_json(stack.COLLECTOR_HTTP_URL, "logs", payload())
    elapsed = round(time.monotonic() - began, 3)
    time.sleep(2.0)
    after = stack.raw_stats("collector", env)
    delta = stack.counter_delta(before, after)
    state = stack.container_state("collector", env)
    accepted = sink_accepted()
    return {
        "http_status": result["http_status"],
        "logs_sent": LOGS,
        "post_s": elapsed,
        "distribution": variant.options["distribution"],
        "processor": variant.options["processor"],
        "startup_s": startup_seconds(env),
        "idle_rss_bytes": idle_rss,
        "heap_alloc_bytes": heap.get("otelcol_process_runtime_heap_alloc_bytes"),
        "cpu_ns": delta["cpu_ns"],
        "cpu_problems": delta["problems"],
        "sink_accepted": None if accepted is None else round(accepted),
        "stock_reject_exit": validate_distribution(CUSTOM, custom=False),
        "custom_accept_exit": validate_distribution(CUSTOM, custom=True),
        "binary_bytes": meta.get("binary_bytes"),
        "image_bytes": meta.get("image_bytes"),
        "build_s": meta.get("build_s"),
        "collector_running": state["running"],
        "collector_oom": state["oom_killed"],
        "collector_restarts": state["restarts"],
    }


def measure(
    variant: Variant,
    sending: Mapping[str, Any],
    collector_log: str,
    sink_log: str,
) -> dict[str, Any]:
    attributes = log_attributes(sink_log)
    stamped = sum(1 for record in attributes if record.get(STAMP) == "true")
    kept = sum(1 for record in attributes if record.get("keep") == "yes")
    return {
        "delivered": sum(export_requests(sink_log, "logs")),
        "records_printed": len(attributes),
        "stamped": stamped,
        "kept": kept,
    }


def validate_run(variant: Variant, run: Mapping[str, Any]) -> list[str]:
    sending = run["sending"]
    measured = run["measured"]
    notes: list[str] = []
    if sending.get("http_status") != 200:
        notes.append(f"receiver returned {sending.get('http_status')}")
    if sending.get("stock_reject_exit") == 0:
        notes.append("the stock binary accepted the custom processor")
    if sending.get("custom_accept_exit") != 0:
        notes.append("the custom binary rejected its own processor")
    if sending.get("cpu_problems"):
        notes.append(f"cpu delta: {sending.get('cpu_problems')}")
    if not sending.get("collector_running"):
        notes.append("the collector is not running")
    if sending.get("collector_oom"):
        notes.append("the collector was OOM-killed")
    if sending.get("collector_restarts"):
        notes.append(f"the collector restarted {sending['collector_restarts']} times")
    if measured.get("delivered") != LOGS:
        notes.append(f"the sink log counted {measured.get('delivered')} records")
    if sending.get("sink_accepted") != LOGS:
        notes.append(f"the sink accepted {sending.get('sink_accepted')}")
    if measured.get("kept") != LOGS:
        notes.append(f"records keeping the original attribute: {measured.get('kept')}")
    if variant.options["processor"]:
        if measured.get("stamped") != LOGS:
            notes.append(f"stamped records were {measured.get('stamped')}")
    elif measured.get("stamped"):
        notes.append(f"pass-through stamped {measured.get('stamped')} records")
    if sending.get("binary_bytes") in (None, 0):
        notes.append("the custom binary size was not recorded")
    return notes


QUESTION = Question(
    id="L4-04",
    title="What does it take to run a processor you wrote yourself?",
    level=4,
    theme="collector",
    signals=("logs",),
    variants=VARIANTS,
    send=send,
    measure=measure,
    validate_run=validate_run,
    repetitions=3,
    rotate_variants=True,
    columns=(
        Column("HTTP", "sending.http_status"),
        Column("Delivered", "measured.delivered"),
        Column("Stamped", "measured.stamped"),
        Column("Kept", "measured.kept"),
        Column("Startup s", "sending.startup_s", digits=3),
        Column("Idle RSS", "sending.idle_rss_bytes"),
        Column("Heap", "sending.heap_alloc_bytes"),
        Column("Post s", "sending.post_s", digits=3),
        Column("CPU ns", "sending.cpu_ns"),
        Column("Stock reject", "sending.stock_reject_exit"),
        Column("Custom accept", "sending.custom_accept_exit"),
        Column("Binary B", "sending.binary_bytes"),
        Column("Image B", "sending.image_bytes"),
        Column("Build s", "sending.build_s", digits=1),
    ),
    interpretation="""
The official binary and the custom one did not disagree about delivery.
Every variant accepted the POST, the sink printed 1000 records, and all
1000 still carried `keep=yes`. The stamp appeared only when the
`experiment` processor was in the pipeline: 1000 of 1000 there, 0 on both
pass-throughs. `otelcol-contrib validate` exited 1 on that config; the
custom image exited 0.

The cost that shows up in the numbers is the artifact, not the burst. The
custom binary is 31719586 bytes, the image 42580242, and the image build
took 87.9 s. Idle RSS on the stock contrib image was about 44 MB; the
custom image, which contains only the components in the OCB manifest, sat
near 12 MB. CPU and post time on 1000 logs overlap across the three
variants. Owning the processor means owning that binary: a new image, a
module graph (`go version -m`), and a rebuild whenever core, contrib, or
OCB moves. This run does not turn that ownership into a deploy-time number.
""",
    does_not_say="""
Measured on Darwin arm64 with Docker Desktop, one custom image
(`ocb v0.162.0`, Go 1.26.0) and `otelcol-contrib 0.162.0`. The first
session was invalid only because a Prometheus query ran before the sink
counter was scraped; the sink log already showed 1000 records. That
session is kept in `results/invalid-prometheus-query/`. The reader now
uses the sink's own `:8888` text. Multi-arch build, an SBOM, and the
calendar time to ship a custom distribution are outside the run. CPU on
this burst is noisy.
""",
)
