import json
from pathlib import Path

from opentelemetry_experiments.core import stack

LOG = """\
collector-1  | 2026-01-01T00:00:00.123Z\tinfo\tservice@v0.162.0/service.go:1\tStarting
collector-1  | 2026-01-01T00:00:01.000Z\twarn\tinternal/accumulator.go:298\tMisaligned
collector-1  | 2026-01-01T00:00:01.100Z\twarn\tinternal/accumulator.go:302\tDropped
collector-1  | 2026-01-01T00:00:02.000Z\terror\texporterhelper/queue.go:1\tfailed
"""


def test_log_summary_counts_by_level():
    assert stack.log_summary(LOG) == {
        "lines": 4,
        "warn_lines": 2,
        "error_lines": 1,
    }


def test_log_summary_of_a_quiet_collector_is_all_zeros():
    quiet = "collector-1  | 2026-01-01T00:00:00Z\tinfo\tservice.go:1\tStarting\n"

    assert stack.log_summary(quiet) == {"lines": 1, "warn_lines": 0, "error_lines": 0}


def test_timestamps_are_stripped_so_two_runs_can_be_diffed():
    stripped = stack.LOG_TIMESTAMP.sub("", LOG)

    assert "2026-01-01T00:00:00.123Z" not in stripped
    assert "Starting" in stripped
    assert stripped.count("\n") == LOG.count("\n")


def test_the_environment_carries_one_config_per_service():
    environment = stack.environment(collector_config="./config/other.yaml")

    assert environment["COLLECTOR_CONFIG"] == "./config/other.yaml"
    assert environment["SINK_CONFIG"] == stack.SINK_CONFIG
    assert environment["PROMETHEUS_CONFIG"] == stack.PROMETHEUS_CONFIG
    assert "GATEWAY_CONFIG" not in environment


def test_the_environment_carries_an_opt_in_gateway_config():
    environment = stack.environment(gateway_config="./config/gateway.yaml")

    assert environment["GATEWAY_CONFIG"] == "./config/gateway.yaml"
    assert stack.gateway_enabled(environment) is True
    assert stack.gateway_enabled(stack.environment()) is False


def test_the_environment_carries_the_directory_the_collector_reads_files_from():
    default = stack.environment()
    overridden = stack.environment(input_directory="./questions/L2-04/input")

    assert default["COLLECTOR_INPUT_DIR"] == stack.COLLECTOR_INPUT_DIR
    assert overridden["COLLECTOR_INPUT_DIR"] == "./questions/L2-04/input"


def test_the_environment_passes_extra_variables_to_the_collector():
    environment = stack.environment(extra={"L1_03_SERVICE": "from-the-environment"})

    assert environment["L1_03_SERVICE"] == "from-the-environment"
    assert environment["COLLECTOR_CONFIG"] == stack.COLLECTOR_DEBUG_CONFIG
    assert json.loads(environment[stack.COLLECTOR_ENVIRONMENT]) == {
        "L1_03_SERVICE": "from-the-environment"
    }


def test_the_collector_has_no_memory_ceiling_unless_a_variant_sets_one():
    assert stack.environment()["COLLECTOR_MEM_LIMIT"] == stack.NO_MEMORY_LIMIT
    limited = stack.environment(collector_memory_limit="512m")

    assert limited["COLLECTOR_MEM_LIMIT"] == "512m"


class Completed:
    def __init__(self, stdout):
        self.stdout = stdout


def test_container_state_reads_running_oom_and_restarts(monkeypatch):
    monkeypatch.setattr(stack, "compose", lambda *args, **kwargs: Completed("abc\n"))
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        return Completed('{"Running":true,"OOMKilled":false} 0\n')

    monkeypatch.setattr(stack, "command", command)

    assert stack.container_state() == {
        "running": True,
        "oom_killed": False,
        "restarts": 0,
    }
    assert calls[0][-1] == "abc"


def test_container_state_of_a_missing_container_is_not_running(monkeypatch):
    monkeypatch.setattr(stack, "compose", lambda *args, **kwargs: Completed(""))

    assert stack.container_state() == {
        "running": False,
        "oom_killed": None,
        "restarts": None,
    }


def test_compose_passes_extra_variables_into_the_collector(monkeypatch):
    captured = {}

    def command(*args, **kwargs):
        override = args[args.index("--file", 4) + 1]
        captured.update(json.loads(Path(override).read_text(encoding="utf-8")))

    monkeypatch.setattr(stack, "command", command)

    stack.compose(
        "config",
        env=stack.environment(extra={"SERVICE_NAME": "from-the-environment"}),
    )

    assert captured == {
        "services": {
            "collector": {"environment": {"SERVICE_NAME": "from-the-environment"}}
        }
    }


def test_save_logs_creates_the_directory(tmp_path):
    destination = tmp_path / "logs" / "valid-r1.log"

    stack.save_logs(destination, "a line\n")

    assert destination.read_text(encoding="utf-8") == "a line\n"


def test_save_logs_strips_the_trailing_spaces_the_hooks_would_rewrite(tmp_path):
    destination = tmp_path / "valid-r1.log"

    stack.save_logs(destination, "a line   \nanother\t\n")

    assert destination.read_text(encoding="utf-8") == "a line\nanother\n"


def test_save_logs_of_an_empty_log_writes_an_empty_file(tmp_path):
    destination = tmp_path / "valid-r1.log"

    stack.save_logs(destination, "")

    assert destination.read_text(encoding="utf-8") == ""


STATS = {
    "cpu_stats": {"cpu_usage": {"total_usage": 1000}},
    "networks": {"eth0": {"rx_bytes": 50, "tx_bytes": 7}},
}


def reading(container="abc", cpu=1000, rx=50):
    return {
        "container_id": container,
        "cpu_ns": cpu,
        "rx_bytes": {"eth0": rx},
        "tx_bytes": {"eth0": 0},
    }


def test_raw_stats_keep_cumulative_cpu_and_bytes_per_interface():
    parsed = stack.parse_raw_stats("abc", STATS)

    assert parsed == {
        "container_id": "abc",
        "cpu_ns": 1000,
        "rx_bytes": {"eth0": 50},
        "tx_bytes": {"eth0": 7},
    }


def test_raw_stats_without_networks_have_no_interfaces():
    parsed = stack.parse_raw_stats("abc", {"cpu_stats": STATS["cpu_stats"]})

    assert parsed["rx_bytes"] == {}


def test_counter_delta_is_the_cost_of_the_window():
    delta = stack.counter_delta(reading(), reading(cpu=4000, rx=850))

    assert delta == {"cpu_ns": 3000, "rx_bytes": {"eth0": 800}, "problems": []}


def test_counter_delta_names_regressions_and_a_new_container():
    delta = stack.counter_delta(reading(), reading(container="def", cpu=10, rx=1))

    assert delta["problems"] == [
        "the container changed between readings",
        "the CPU counter went backwards",
        "a network counter went backwards",
    ]


def test_counter_delta_names_a_changed_interface_set():
    after = reading()
    after["rx_bytes"] = {"eth0": 50, "eth1": 3}

    assert stack.counter_delta(reading(), after)["problems"] == [
        "the network interfaces changed between readings"
    ]


def test_docker_socket_reads_the_active_context(monkeypatch):
    monkeypatch.setattr(
        stack,
        "command",
        lambda *args, **kwargs: Completed("unix:///tmp/docker.sock\n"),
    )

    assert stack.docker_socket() == "/tmp/docker.sock"


def test_engine_get_speaks_http_over_a_unix_socket():
    import socketserver
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"Version": "29.8.2", "ApiVersion": "1.56"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(socketserver.UnixStreamServer):
        def get_request(self):
            request, _ = super().get_request()
            return request, ("local", 0)

    directory = tempfile.TemporaryDirectory(dir="/tmp")
    path = f"{directory.name}/engine.sock"
    server = Server(path, Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        assert stack.engine_version(path) == {"engine": "29.8.2", "api": "1.56"}
    finally:
        thread.join(timeout=5)
        server.server_close()
        directory.cleanup()


def test_validate_config_reports_exit_code_and_output(monkeypatch):
    import subprocess

    from opentelemetry_experiments.core import stack

    seen = {}

    def fake(*args, env=None, check=True):
        seen["args"] = args
        seen["config"] = env["COLLECTOR_CONFIG"]
        seen["check"] = check
        return subprocess.CompletedProcess(args, 1, "", "Error: bad\n")

    monkeypatch.setattr(stack, "compose", fake)
    result = stack.validate_config("./x.yaml")

    assert result["exit_code"] == 1
    assert result["stderr"] == "Error: bad\n"
    assert seen["config"] == "./x.yaml"
    assert seen["check"] is False
    assert seen["args"][:4] == ("run", "--rm", "--no-deps", "collector")
