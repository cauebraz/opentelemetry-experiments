import http.client
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "questions"

COLLECTOR_GRPC = "127.0.0.1:24317"
COLLECTOR_HTTP_URL = "http://127.0.0.1:24318"
COLLECTOR_HEALTH_URL = "http://127.0.0.1:23133"
COLLECTOR_SELF_METRICS_URL = "http://127.0.0.1:28888/metrics"
SINK_HEALTH_URL = "http://127.0.0.1:23134"
SINK_SELF_METRICS_URL = "http://127.0.0.1:28889/metrics"
GATEWAY_HEALTH_URL = "http://127.0.0.1:23135"
GATEWAY_SELF_METRICS_URL = "http://127.0.0.1:28890/metrics"
PROMETHEUS_URL = "http://127.0.0.1:29090"

COLLECTOR_DEBUG_CONFIG = "./config/collector-debug.yaml"
SINK_CONFIG = "./config/sink.yaml"
SINK_DETAILED_CONFIG = "./config/sink-detailed.yaml"
GATEWAY_CONFIG = ""
PROMETHEUS_CONFIG = "./config/prometheus.yaml"
COLLECTOR_INPUT_DIR = "./config/input"
NO_MEMORY_LIMIT = "0"

OTELCOL_VERSION = "0.162.0"
PROMETHEUS_VERSION = "v3.15.0"
COLLECTOR_ENVIRONMENT = "OPENTELEMETRY_EXPERIMENTS_COLLECTOR_ENVIRONMENT"


def command(
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        check=check,
        text=True,
        capture_output=True,
    )


def gateway_enabled(env: dict[str, str] | None) -> bool:
    return bool((env or {}).get("GATEWAY_CONFIG"))


def compose(
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    profile: list[str] = []
    if gateway_enabled(env):
        profile = ["--profile", "gateway"]
    extra = json.loads((env or {}).get(COLLECTOR_ENVIRONMENT, "{}"))
    if not extra:
        return command("docker", "compose", *profile, *args, env=env, check=check)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        encoding="utf-8",
    ) as override:
        json.dump({"services": {"collector": {"environment": extra}}}, override)
        override.flush()
        return command(
            "docker",
            "compose",
            *profile,
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            override.name,
            *args,
            env=env,
            check=check,
        )


def environment(
    collector_config: str = COLLECTOR_DEBUG_CONFIG,
    sink_config: str = SINK_CONFIG,
    prometheus_config: str = PROMETHEUS_CONFIG,
    input_directory: str = COLLECTOR_INPUT_DIR,
    extra: Mapping[str, str] | None = None,
    collector_memory_limit: str = NO_MEMORY_LIMIT,
    collector_feature_gates: str = "",
    gateway_config: str = GATEWAY_CONFIG,
    collector_build_context: str = ".",
    collector_dockerfile: str = "Dockerfile.collector",
    collector_image: str = "",
) -> dict[str, str]:
    """`extra` reaches the collector process, so a config can read `${env:NAME}`."""
    collector_environment = dict(extra or {})
    values = {
        "COLLECTOR_CONFIG": collector_config,
        "SINK_CONFIG": sink_config,
        "PROMETHEUS_CONFIG": prometheus_config,
        "COLLECTOR_INPUT_DIR": input_directory,
        "COLLECTOR_MEM_LIMIT": collector_memory_limit,
        "COLLECTOR_FEATURE_GATES": collector_feature_gates,
        "COLLECTOR_BUILD_CONTEXT": collector_build_context,
        "COLLECTOR_DOCKERFILE": collector_dockerfile,
        "COLLECTOR_IMAGE": collector_image or "opentelemetry-experiments-collector",
        COLLECTOR_ENVIRONMENT: json.dumps(
            collector_environment,
            sort_keys=True,
        ),
    }
    if gateway_config:
        values["GATEWAY_CONFIG"] = gateway_config
    return os.environ | values | collector_environment


def start(env: dict[str, str]) -> None:
    """Sink, then optional gateway, then agent and Prometheus."""
    from opentelemetry_experiments.core.prometheus import wait_for_url

    stop(env)
    compose("up", "--detach", "sink", env=env)
    wait_for_url(SINK_HEALTH_URL)
    if gateway_enabled(env):
        compose("up", "--detach", "gateway", env=env)
        wait_for_url(GATEWAY_HEALTH_URL)
    compose("up", "--detach", env=env)
    wait_for_url(COLLECTOR_HEALTH_URL)
    wait_for_url(f"{PROMETHEUS_URL}/-/ready")


def stop(env: dict[str, str] | None = None) -> None:
    compose("down", "--volumes", "--remove-orphans", env=env, check=False)


def pause_sink(env: dict[str, str] | None = None) -> None:
    compose("pause", "sink", env=env)


def unpause_sink(env: dict[str, str] | None = None) -> None:
    compose("unpause", "sink", env=env)


def stop_sink(env: dict[str, str] | None = None) -> None:
    compose("stop", "sink", env=env)


def start_sink(env: dict[str, str] | None = None) -> None:
    compose("start", "sink", env=env)


def pause_gateway(env: dict[str, str] | None = None) -> None:
    compose("pause", "gateway", env=env)


def unpause_gateway(env: dict[str, str] | None = None) -> None:
    compose("unpause", "gateway", env=env)


def container_state(
    service: str = "collector",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Whether the service is still running and whether the kernel killed it."""
    container_id = compose("ps", "--all", "--quiet", service, env=env).stdout.strip()
    if not container_id:
        return {"running": False, "oom_killed": None, "restarts": None}
    output = command(
        "docker",
        "inspect",
        "--format",
        "{{json .State}} {{.RestartCount}}",
        container_id,
    ).stdout
    state, _, restarts = output.strip().rpartition(" ")
    parsed = json.loads(state)
    return {
        "running": bool(parsed.get("Running")),
        "oom_killed": bool(parsed.get("OOMKilled")),
        "restarts": int(restarts),
    }


STATS_FIELDS = ("CPUPerc", "MemPerc", "MemUsage")


def container_stats(
    service: str = "collector",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    container_id = compose("ps", "--quiet", service, env=env).stdout.strip()
    output = command(
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        container_id,
    ).stdout
    sample = json.loads(output)
    return {key: sample[key] for key in STATS_FIELDS if key in sample}


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 10.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def docker_socket() -> str:
    """The Unix socket of the active Docker context."""
    host = command(
        "docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"
    ).stdout.strip()
    if not host.startswith("unix://"):
        raise RuntimeError(f"the Docker context is not a Unix socket: {host}")
    return host.removeprefix("unix://")


def engine_get(path: str, socket_path: str | None = None) -> Any:
    connection = UnixConnection(socket_path or docker_socket())
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise RuntimeError(f"Docker Engine {path} answered {response.status}")
        return json.loads(body)
    finally:
        connection.close()


def engine_version(socket_path: str | None = None) -> dict[str, str]:
    version = engine_get("/version", socket_path)
    return {"engine": version["Version"], "api": version["ApiVersion"]}


def parse_raw_stats(container_id: str, stats: Mapping[str, Any]) -> dict[str, Any]:
    networks = stats.get("networks") or {}
    return {
        "container_id": container_id,
        "cpu_ns": stats["cpu_stats"]["cpu_usage"]["total_usage"],
        "rx_bytes": {name: values["rx_bytes"] for name, values in networks.items()},
        "tx_bytes": {name: values["tx_bytes"] for name, values in networks.items()},
    }


def raw_stats(
    service: str = "collector",
    env: dict[str, str] | None = None,
    socket_path: str | None = None,
) -> dict[str, Any]:
    """Cumulative CPU nanoseconds and per-interface bytes, read from the Engine."""
    container_id = compose("ps", "--quiet", service, env=env).stdout.strip()
    if not container_id:
        raise RuntimeError(f"{service} is not running")
    stats = engine_get(
        f"/containers/{container_id}/stats?stream=false&one-shot=true", socket_path
    )
    return parse_raw_stats(container_id, stats)


def counter_delta(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    """What one window cost, or why the two readings cannot be compared."""
    problems = []
    if before["container_id"] != after["container_id"]:
        problems.append("the container changed between readings")
    if set(before["rx_bytes"]) != set(after["rx_bytes"]):
        problems.append("the network interfaces changed between readings")
    cpu = after["cpu_ns"] - before["cpu_ns"]
    rx = {
        name: after["rx_bytes"][name] - before["rx_bytes"].get(name, 0)
        for name in after["rx_bytes"]
    }
    if cpu < 0:
        problems.append("the CPU counter went backwards")
    if any(value < 0 for value in rx.values()):
        problems.append("a network counter went backwards")
    return {"cpu_ns": cpu, "rx_bytes": rx, "problems": problems}


LOG_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b")
LOG_LEVEL = re.compile(r"\t(debug|info|warn|error|dpanic|panic|fatal)\t")
COMPOSE_PREFIX = re.compile(r"(?m)^[^|\n]*\| ?")


def validate_config(collector_config: str) -> dict[str, Any]:
    """`otelcol validate` on one config, in a container that exits with it."""
    env = environment(collector_config=collector_config)
    began = time.monotonic()
    result = compose(
        "run",
        "--rm",
        "--no-deps",
        "collector",
        "validate",
        "--config=/etc/otelcol-contrib/config.yaml",
        env=env,
        check=False,
    )
    return {
        "exit_code": result.returncode,
        "stderr": result.stderr,
        "stdout": result.stdout,
        "seconds": round(time.monotonic() - began, 2),
    }


def logs(service: str = "collector", env: dict[str, str] | None = None) -> str:
    """The service log with execution timestamps removed, so runs are diffable."""
    result = compose("logs", "--no-color", service, env=env)
    return LOG_TIMESTAMP.sub("", result.stdout)


def unprefixed(text: str) -> str:
    """The log without the `service-1  | ` prefix docker compose adds."""
    return COMPOSE_PREFIX.sub("", text)


def log_summary(text: str) -> dict[str, int]:
    levels = [match.group(1) for match in LOG_LEVEL.finditer(text)]
    return {
        "lines": text.count("\n"),
        "warn_lines": levels.count("warn"),
        "error_lines": sum(levels.count(level) for level in ("error", "fatal")),
    }


def save_logs(destination: Path, text: str) -> None:
    """Trailing spaces are stripped, or the repository's hooks rewrite the file."""
    body = "\n".join(line.rstrip() for line in text.splitlines())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f"{body}\n" if body else "", encoding="utf-8")
