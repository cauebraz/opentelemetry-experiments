import gzip
import importlib.util
import json

from opentelemetry_experiments.core import stack

DIRECTORY = stack.ROOT / "questions" / "L1-04-grpc-vs-http-receiver"
spec = importlib.util.spec_from_file_location(
    "l1_04_question", DIRECTORY / "question.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def stats(container_id="c1", cpu=0, rx=0, interfaces=None):
    return {
        "container_id": container_id,
        "cpu_ns": cpu,
        "rx_bytes": interfaces if interfaces is not None else {"eth0": rx},
        "tx_bytes": {},
    }


def test_the_workload_is_deterministic_and_parses_to_protobuf():
    document = module.payload(3)
    assert document == module.payload(3)
    parsed = module.message(document)
    points = parsed.resource_metrics[0].scope_metrics[0].metrics[0].sum.data_points
    assert len(points) == module.POINTS_PER_REQUEST
    assert points[0].time_unix_nano == (
        module.START_UNIX_NANO + 3 * module.POINTS_PER_REQUEST * module.STEP_NANO
    )
    assert module.POINTS == 100_000


def test_variants_cover_both_protocols_and_json():
    options = [
        (v.options["protocol"], v.options["encoding"], v.options["gzip"])
        for v in module.VARIANTS
    ]
    assert options == [
        ("grpc", "proto", False),
        ("grpc", "proto", True),
        ("http", "proto", False),
        ("http", "proto", True),
        ("http", "json", False),
    ]
    assert module.QUESTION.repetitions == len(module.VARIANTS)
    assert module.QUESTION.rotate_variants


def test_http_bodies_match_their_headers():
    document = module.payload(0, points=2)
    parsed = module.message(document)
    body, headers = module.http_body("proto", True, document, parsed)
    assert headers == {
        "Content-Type": "application/x-protobuf",
        "Content-Encoding": "gzip",
    }
    assert gzip.decompress(body) == parsed.SerializeToString()
    body, headers = module.http_body("json", False, document, parsed)
    assert headers == {"Content-Type": "application/json"}
    assert json.loads(body) == document


def test_summary_subtracts_idle_cpu_and_rx():
    idle = {"cpu_ns": 1_000, "rx_bytes": 100, "problems": []}
    work = {"cpu_ns": 10_000_000, "rx_bytes": 5_000_000, "problems": ["x"]}
    summary = module.summarize(idle, work, 1.0, 10.0)
    assert summary["collector_cpu_ns_per_point"] == 99.9
    assert summary["background_rx_bytes"] == 1000
    assert summary["net_rx_bytes"] == 4_999_000
    assert summary["window_problems"] == ["workload: x"]


def test_window_flags_other_interfaces():
    before = stats(interfaces={"eth0": 0, "eth1": 0})
    after = stats(interfaces={"eth0": 10, "eth1": 3})
    assert module.window(before, after)["problems"] == ["bytes arrived on eth1"]


def test_quiesce_waits_for_delivery_then_flat_rx(monkeypatch):
    monkeypatch.setattr(module, "POLL_SECONDS", 0)
    monkeypatch.setattr(
        module.debuglog, "export_requests", lambda text, signal: [100] if text else []
    )
    values = iter([10, 20, 20, 20, 20])
    logs = iter(["", "x"])
    last, quiet = module.quiesce(
        lambda: {"rx_bytes": {"eth0": next(values)}}, lambda: next(logs), 100
    )
    assert quiet
    assert last["rx_bytes"] == {"eth0": 20}


def valid_run(probe="RemoteDisconnected", delivered=100_000):
    return {
        "sending": {
            "window_problems": [],
            "background_rx_bytes": 800,
            "rx_bytes": 7_000_000,
            "failed_requests": 0,
            "quiescent": True,
            "prometheus_restored": True,
            "json_on_grpc_port": probe,
        },
        "measured": {"delivered_points": delivered},
        "counters": {"sink": {"metrics": {"receiver_accepted": delivered}}},
    }


def test_a_clean_run_is_valid():
    assert module.validate_run(module.VARIANTS[0], valid_run()) == []


def test_an_accepted_probe_adds_one_point():
    run = valid_run(probe="HTTP 200", delivered=100_001)
    assert module.validate_run(module.VARIANTS[0], run) == []
    assert module.validate_run(module.VARIANTS[0], valid_run(probe="HTTP 200"))


def test_invalid_runs_name_their_cause():
    run = valid_run()
    run["sending"]["background_rx_bytes"] = 90_000
    run["sending"]["failed_requests"] = 2
    run["sending"]["prometheus_restored"] = False
    notes = module.validate_run(module.VARIANTS[0], run)
    assert "2 requests failed" in notes
    assert "Prometheus did not come back" in notes
    assert any(note.startswith("the collector's idle traffic") for note in notes)
