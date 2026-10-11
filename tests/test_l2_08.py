import importlib.util
import re

from opentelemetry_experiments.core import stack

DIRECTORY = stack.ROOT / "questions" / "L2-08-persistent-queue"
PATH = DIRECTORY / "question.py"
SPEC = importlib.util.spec_from_file_location("l2_08_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def by_name(name):
    return next(variant for variant in module.VARIANTS if variant.name == name)


def test_every_record_names_its_request_and_position():
    assert module.record_bodies(3)[:2] == ["r03-i00", "r03-i01"]
    assert len(module.record_bodies(3)) == module.RECORDS_PER_REQUEST


def test_one_payload_carries_one_request_of_records():
    payload = module.request_payload(4)
    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]

    assert [record["body"]["stringValue"] for record in records] == (
        module.record_bodies(4)
    )


def test_duplicates_count_extra_copies_only():
    assert module.duplicates(["a", "b", "a", "a"]) == 2
    assert module.duplicates(["a", "b"]) == 0


def test_unknown_counts_bodies_never_sent():
    assert module.unknown(["r00-i00", "r99-i00"]) == 1


def test_measure_reads_drop_lines_and_starts():
    ready = "collector-1  | 2026\tinfo\tservice\tEverything is ready.\n"
    drop = "collector-1  | 2026\terror\tx\tExporting failed. Dropping data.\t"
    collector_log = (
        ready
        + drop
        + '{"dropped_items": 10}\n'
        + drop
        + '{"dropped_items": 7}\n'
        + ready
    )

    result = module.measure(by_name("memory-sigterm"), {}, collector_log, "")

    assert result["drop_log_lines"] == 2
    assert result["dropped_items"] == 17
    assert result["starts_logged"] == 2
    assert result["records_printed"] == 0


def run(accepted=100, queued=10, starts=2, printed=100, delivered=100, unknown=0):
    return {
        "sending": {
            "accepted_before_fault": accepted,
            "queue_before_fault": queued,
            "delivered": delivered,
        },
        "measured": {
            "starts_logged": starts,
            "records_printed": printed,
            "unknown_records": unknown,
        },
    }


def test_a_full_queue_and_one_restart_is_valid():
    assert module.validate_run(by_name("volume-sigkill"), run()) == []


def test_a_queue_that_never_filled_invalidates_the_run():
    notes = module.validate_run(by_name("volume-sigkill"), run(queued=7))

    assert notes == ["the queue held 7 requests before the fault, not 10"]


def test_a_missing_restart_or_count_mismatch_invalidates_the_run():
    notes = module.validate_run(
        by_name("memory-sigkill"), run(starts=1, printed=90, unknown=1)
    )

    assert len(notes) == 3


def test_every_variant_runs_on_the_same_image():
    assert {variant.collector_image for variant in module.VARIANTS} == {module.IMAGE}


def test_the_persistent_config_differs_from_memory_only_in_storage():
    volume = (stack.ROOT / module.VOLUME).read_text(encoding="utf-8")
    memory = (stack.ROOT / module.MEMORY).read_text(encoding="utf-8")
    storage = (
        "    sending_queue:\n      storage: file_storage\n",
        "  file_storage:\n    directory: /var/lib/otelcol/file_storage\n"
        "    create_directory: true\n",
        "    - file_storage\n",
    )

    for block in storage:
        assert block in volume
        volume = volume.replace(block, "", 1)
    assert volume == memory


def test_a_recreated_collector_logs_one_start_and_the_others_two():
    assert module.expected_starts(by_name("volume-recreated")) == 1
    assert module.expected_starts(by_name("volume-sigkill")) == 2

    assert module.validate_run(by_name("volume-recreated"), run(starts=1)) == []


def test_the_question_image_pins_the_same_collector_as_the_stack():
    question = (DIRECTORY / "Dockerfile").read_text(encoding="utf-8")
    stock = (stack.ROOT / "Dockerfile.collector").read_text(encoding="utf-8")

    for name in ("OTELCOL_VERSION", "OTELCOL_SHA256_AMD64", "OTELCOL_SHA256_ARM64"):
        pattern = rf"ARG {name}=(\S+)"
        assert re.search(pattern, question).group(1) == re.search(pattern, stock).group(
            1
        )
    assert question.split("\n", 1)[0] == stock.split("\n", 1)[0]
