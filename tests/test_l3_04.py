import importlib.util
from collections import Counter

from opentelemetry_experiments.core import stack

PATH = stack.ROOT / "questions" / "L3-04-structured-vs-text-logs" / "question.py"
SPEC = importlib.util.spec_from_file_location("l3_04_question", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_the_workload_has_five_routes_and_twenty_errors_each():
    events = module.events()
    by_route = Counter(event.route for event in events)
    errors = Counter(event.route for event in events if event.outcome == "error")

    assert len(events) == 1000
    assert set(by_route.values()) == {200}
    assert set(errors.values()) == {20}
    assert len({event.request_id for event in events}) == 1000


def test_drift_is_ten_records_with_the_traffic_error_ratio():
    drifted = [event for event in module.events() if event.index in module.DRIFTED]

    assert len(drifted) == 10
    assert sum(event.outcome == "error" for event in drifted) == 1


def test_every_variant_describes_the_same_events():
    for name in ("text-canonical", "text-drift-1pct", "structured"):
        records = module.records(name)
        sequences = [
            next(
                item["value"]["intValue"]
                for item in record["attributes"]
                if item["key"] == "event.sequence"
            )
            for record in records
        ]
        assert sequences == [str(index) for index in range(1000)]


def test_only_structured_records_carry_the_route_attribute():
    def keys(name):
        return {item["key"] for item in module.records(name)[0]["attributes"]}

    assert "http.route" in keys("structured")
    assert "http.route" not in keys("text-canonical")
    assert "request_id" not in module.COUNT_DIMENSIONS


def test_canonical_text_matches_the_parser_and_drift_does_not():
    events = module.events()
    canonical = module.text_body(events[1], drift=False)
    drifted = module.text_body(events[27], drift=True)

    assert module.PARSER.search(canonical).group("route") == events[1].route
    assert module.PARSER.search(drifted) is None


def test_accounting_finds_duplicates_unknowns_and_parsed_records():
    printed = [
        {"event.sequence": "0", "http.route": "/cart", "outcome": "ok"},
        {"event.sequence": "0", "http.route": "/cart", "outcome": "ok"},
        {"event.sequence": "1"},
        {"event.sequence": "5000"},
    ]

    result = module.accounting(printed, expected=3)

    assert result["delivered_records"] == 4
    assert result["unique_sequences"] == 2
    assert result["duplicates"] == 1
    assert result["unknown_sequences"] == 1
    assert result["missing_sequences"] == 1
    assert result["parsed_records"] == 2


def test_miscounted_compares_each_category_with_the_traffic():
    truth = Counter({("/cart", "ok"): 3, ("/cart", "error"): 1})
    observed = {("/cart", "ok"): 2, ("/cart", "error"): 1, ("unparsed", "unparsed"): 1}

    assert module.miscounted(observed, truth) == 1


def valid_run(**measured):
    base = {
        "delivered_records": 1000,
        "unique_sequences": 1000,
        "duplicates": 0,
        "unknown_sequences": 0,
        "missing_sequences": 0,
    }
    return {
        "sending": {
            "http_status": 200,
            "records_sent": 1000,
            "request_bytes": 100,
            "batch_item_bytes": 90.0,
            "control_count": 1000.0,
        },
        "counters": {
            "collector": {"logs": {"receiver_accepted": 1000}},
            "sink": {"logs": {"receiver_accepted": 1000}},
        },
        "measured": {**base, **measured},
    }


def test_a_clean_run_is_valid_even_when_records_are_unparsed():
    run = valid_run(parsed_records=990)

    assert module.validate_run(module.VARIANTS[1], run) == []


def test_validity_names_every_failure():
    run = valid_run(duplicates=1, unknown_sequences=1, missing_sequences=1)
    run["sending"].update(
        {"http_status": 503, "batch_item_bytes": None, "control_count": 999.0}
    )
    run["counters"]["sink"]["logs"]["receiver_accepted"] = 999

    notes = " | ".join(module.validate_run(module.VARIANTS[0], run))

    for fragment in (
        "HTTP 503",
        "sink counted 999",
        "duplicated",
        "unknown",
        "missing",
        "control count",
        "batch_item_bytes",
    ):
        assert fragment in notes


def test_every_variant_shares_one_config_and_the_detailed_sink():
    configs = {variant.collector_config for variant in module.VARIANTS}

    assert len(configs) == 1
    assert all(
        variant.sink_config == stack.SINK_DETAILED_CONFIG for variant in module.VARIANTS
    )
