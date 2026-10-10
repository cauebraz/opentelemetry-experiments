import importlib.util

from opentelemetry_experiments.core import otlp, prometheus, stack

PATH = stack.ROOT / "questions" / "L4-05-remote-write-vs-scrape" / "question.py"
SPEC = importlib.util.spec_from_file_location("l4_05_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_payload_is_one_exponential_histogram_with_two_hundred_observations():
    body = module.payload()
    metric = body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    point = metric["exponentialHistogram"]["dataPoints"][0]

    assert metric["name"] == module.METRIC
    assert point["count"] == "200"
    assert point["sum"] == module.SUM_VALUE
    assert point["positive"]["bucketCounts"] == ["50", "50", "50", "50"]


def test_variants_cover_scrape_and_remote_write_healthy_and_down():
    names = [variant.name for variant in module.VARIANTS]
    assert names == [
        "scrape-healthy",
        "scrape-down",
        "remote-write-healthy",
        "remote-write-down",
    ]
    by_name = {variant.name: variant for variant in module.VARIANTS}
    assert by_name["scrape-healthy"].options["stop_prometheus"] is False
    assert by_name["scrape-down"].options["stop_prometheus"] is True
    assert "remote-write" in by_name["remote-write-healthy"].collector_config


def test_parse_prometheus_text_sums_samples_by_name():
    text = (
        "# HELP otelcol_exporter_queue_size Queue size\n"
        "# TYPE otelcol_exporter_queue_size gauge\n"
        'otelcol_exporter_queue_size{exporter="prometheusremotewrite"} 3\n'
        'otelcol_exporter_queue_size{exporter="otlp"} 1\n'
    )

    assert prometheus.parse_prometheus_text(text)["otelcol_exporter_queue_size"] == 4.0


def test_exponential_histogram_helper_sets_temporality():
    metric = otlp.exponential_histogram_metric(
        "m",
        [otlp.exponential_histogram_point(1, 1.0, 0, 0, 0, [1])],
    )

    assert metric["exponentialHistogram"]["aggregationTemporality"] == otlp.DELTA


def test_metric_name_includes_unit_suffix_from_translation():
    assert module.METRIC_NAME == "l4_05_duration_milliseconds"


def test_payload_uses_fresh_timestamps():
    first = module.payload()
    second = module.payload()
    point_a = first["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0][
        "exponentialHistogram"
    ]["dataPoints"][0]
    point_b = second["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0][
        "exponentialHistogram"
    ]["dataPoints"][0]

    assert point_a["startTimeUnixNano"] != point_b["startTimeUnixNano"]
