from opentelemetry_experiments.core import debuglog, otlp, stack
from opentelemetry_experiments.core.prometheus import counters, wait_until_stable

SIGNALS = ("traces", "metrics", "logs")


def test_one_item_of_each_signal_reaches_the_sink(tmp_path):
    environment = stack.environment(sink_config=stack.SINK_DETAILED_CONFIG)
    trace_id = otlp.hex_id(16)
    metric_name = f"smoke_{otlp.hex_id(4)}"
    log_body = f"smoke-{otlp.hex_id(4)}"
    try:
        stack.start(environment)
        responses = {
            "traces": otlp.post_json(
                stack.COLLECTOR_HTTP_URL,
                "traces",
                otlp.trace_payload([otlp.span("smoke", trace_id=trace_id)]),
            ),
            "metrics": otlp.post_json(
                stack.COLLECTOR_HTTP_URL,
                "metrics",
                otlp.metric_payload(
                    [otlp.gauge_metric(metric_name, [otlp.number_point(1.0)])]
                ),
            ),
            "logs": otlp.post_json(
                stack.COLLECTOR_HTTP_URL,
                "logs",
                otlp.log_payload([otlp.log_record(log_body)]),
            ),
        }
        observed = wait_until_stable(
            lambda: counters(stack.PROMETHEUS_URL, "sink", SIGNALS)
        )
        sink_log = stack.logs("sink", environment)
        stack.save_logs(tmp_path / "sink.log", sink_log)
    finally:
        stack.stop(environment)

    assert {signal: body["http_status"] for signal, body in responses.items()} == {
        signal: 200 for signal in SIGNALS
    }
    assert {signal: observed[signal]["receiver_accepted"] for signal in SIGNALS} == {
        signal: 1.0 for signal in SIGNALS
    }
    assert trace_id in debuglog.spans_by_trace(sink_log)
    assert metric_name in stack.unprefixed(sink_log)
    assert log_body in debuglog.log_bodies(sink_log)
