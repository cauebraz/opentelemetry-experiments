import importlib.util

from opentelemetry_experiments.core import stack

PATH = stack.ROOT / "questions" / "L4-04-custom-processor-with-ocb" / "question.py"
SPEC = importlib.util.spec_from_file_location("l4_04_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_payload_is_one_thousand_logs_with_a_stable_attribute():
    body = module.payload()
    records = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]

    assert len(records) == 1000
    kept = {
        item["key"]: item["value"]["stringValue"]
        for item in records[0]["attributes"]
        if "stringValue" in item["value"]
    }
    assert kept["keep"] == "yes"
    assert len({record["body"]["stringValue"] for record in records}) == 1000


def test_variants_separate_stock_from_the_custom_distribution():
    by_name = {variant.name: variant for variant in module.VARIANTS}

    assert list(by_name) == [
        "stock-pass-through",
        "custom-pass-through",
        "custom-processor",
    ]
    assert by_name["stock-pass-through"].collector_dockerfile == "Dockerfile.collector"
    assert by_name["custom-processor"].collector_dockerfile == "Dockerfile"
    assert by_name["custom-processor"].collector_image.endswith(":0.162.0")
    assert by_name["stock-pass-through"].collector_image == ""
    assert by_name["custom-processor"].options["processor"] is True
    assert by_name["custom-pass-through"].options["processor"] is False
    assert "cases/collector-custom.yaml" in by_name["custom-processor"].collector_config
    assert (
        "collector-passthrough.yaml" in by_name["stock-pass-through"].collector_config
    )
