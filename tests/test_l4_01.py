import importlib.util

from opentelemetry_experiments.core import stack

PATH = stack.ROOT / "questions" / "L4-01-agent-vs-gateway" / "question.py"
SPEC = importlib.util.spec_from_file_location("l4_01_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_payload_has_one_thousand_unique_spans():
    body = module.payload()
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 1000
    assert len({span["traceId"] for span in spans}) == 1000
    assert len({span["spanId"] for span in spans}) == 1000


def test_variants_cover_healthy_gateway_pause_and_sink_pause():
    names = [variant.name for variant in module.VARIANTS]
    assert names == ["healthy", "gateway-paused", "sink-paused"]
    for variant in module.VARIANTS:
        assert variant.gateway_config.endswith("gateway.yaml")
        assert variant.prometheus_config.endswith("prometheus.yaml")


def test_gateway_profile_is_opt_in():
    bare = stack.environment()
    with_gateway = stack.environment(gateway_config=module.GATEWAY_CONFIG)
    assert stack.gateway_enabled(bare) is False
    assert stack.gateway_enabled(with_gateway) is True
