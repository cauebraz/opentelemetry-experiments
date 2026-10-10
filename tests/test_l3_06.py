import importlib.util

from opentelemetry_experiments.core import stack

DIRECTORY = stack.ROOT / "questions" / "L3-06-compression-and-protocol"
spec = importlib.util.spec_from_file_location(
    "l3_06_question", DIRECTORY / "question.py"
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


def test_the_workload_is_deterministic_and_complete():
    first = module.payload(0)
    again = module.payload(0)
    points = first["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]

    assert first == again
    assert len(points["dataPoints"]) == module.POINTS_PER_REQUEST
    assert module.POINTS == 100_000
    assert points["aggregationTemporality"] == "AGGREGATION_TEMPORALITY_CUMULATIVE"


def test_every_variant_sets_its_protocol_and_compression():
    names = [variant.name for variant in module.VARIANTS]
    assert names == ["grpc-none", "grpc-gzip", "http-none", "http-gzip"]
    for variant in module.VARIANTS:
        protocol, compression = variant.name.split("-")
        text = (stack.ROOT / variant.collector_config).read_text()
        assert f"compression: {compression}" in text
        assert ("sink:4317" in text) == (protocol == "grpc")
        assert "send_batch_max_size: 5000" in text
    assert module.QUESTION.repetitions == 4
    assert module.QUESTION.rotate_variants


def test_window_reports_bytes_on_eth0_only():
    result = module.window(stats(cpu=10, rx=5), stats(cpu=40, rx=105))
    assert result == {"cpu_ns": 30, "rx_bytes": 100, "problems": []}


def test_window_flags_unexpected_interfaces_and_identity_change():
    before = stats(interfaces={"eth0": 0, "eth1": 0})
    after = stats("c2", interfaces={"eth0": 10, "eth1": 3})
    problems = module.window(before, after)["problems"]
    assert "the container changed between readings" in problems
    assert "bytes arrived on eth1" in problems


def test_summary_subtracts_the_idle_cpu_rate():
    idle = {
        "collector": {"cpu_ns": 1_000, "rx_bytes": None, "problems": []},
        "sink": {"cpu_ns": 0, "rx_bytes": 100, "problems": []},
    }
    work = {
        "collector": {"cpu_ns": 10_000_000, "rx_bytes": None, "problems": []},
        "sink": {"cpu_ns": 0, "rx_bytes": 5_000_000, "problems": ["x"]},
    }
    summary = module.summarize_windows(idle, work, 1.0, 10.0)
    assert summary["adjusted_cpu_ns"] == 9_990_000
    assert summary["adjusted_cpu_ns_per_point"] == 99.9
    assert summary["background_rx_bytes"] == 1000
    assert summary["net_rx_bytes"] == 4_999_000
    assert summary["rx_bytes_per_point"] == 50.0
    assert summary["window_problems"] == ["workload sink: x"]


def test_quiesce_waits_for_delivery_then_flat_rx(monkeypatch):
    monkeypatch.setattr(module, "POLL_SECONDS", 0)
    values = iter([10, 20, 20, 20, 20, 20])
    logs = iter(["", "x"])
    monkeypatch.setattr(module, "delivered", lambda text: [100] if text else [])

    def read():
        return {"sink": {"rx_bytes": {"eth0": next(values)}}}

    last, quiet = module.quiesce(read, lambda: next(logs), 100)
    assert quiet
    assert last["sink"]["rx_bytes"] == {"eth0": 20}


def valid_run():
    accepted = {"receiver_accepted": 100_000, "receiver_refused": 0}
    return {
        "sending": {
            "window_problems": [],
            "idle_sink_rx_bytes": 1000,
            "background_rx_bytes": 800,
            "non_200": 0,
            "quiescent": True,
            "prometheus_restored": True,
            "sink_rx_bytes": 845_000,
        },
        "measured": {"delivered_points": 100_000, "largest_request": 5000},
        "counters": {
            "collector": {"metrics": accepted | {"exporter_send_failed": 0}},
            "sink": {"metrics": accepted},
        },
    }


def test_a_clean_run_is_valid():
    assert module.validate_run(module.VARIANTS[0], valid_run()) == []


def test_invalid_runs_name_their_cause():
    run = valid_run()
    run["sending"]["background_rx_bytes"] = 9000
    run["sending"]["prometheus_restored"] = False
    run["measured"]["delivered_points"] = 99_500
    run["counters"]["collector"]["metrics"]["exporter_send_failed"] = 500
    notes = module.validate_run(module.VARIANTS[0], run)
    assert any(note.startswith("the sink's idle traffic, 9000 bytes") for note in notes)
    assert "Prometheus did not come back" in notes
    assert "the sink logged 99500 points" in notes
    assert "the collector reported exporter_send_failed 500" in notes
