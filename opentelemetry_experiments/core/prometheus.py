import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from math import isnan
from typing import Any

DEFAULT_URL = "http://127.0.0.1:29090"

SIGNAL_UNITS = {
    "traces": "spans",
    "metrics": "metric_points",
    "logs": "log_records",
}

COUNTER_ROLES = {
    "receiver_accepted": "otelcol_receiver_accepted_{unit}",
    "receiver_refused": "otelcol_receiver_refused_{unit}",
    "exporter_sent": "otelcol_exporter_sent_{unit}",
    "exporter_send_failed": "otelcol_exporter_send_failed_{unit}",
}


def read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def read_text(url: str, timeout: float = 5.0) -> str:
    """Raw body of a URL, used for Collector self-metrics on :8888."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Sum samples by metric name from a Prometheus text exposition."""
    totals: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition("{")
        if not _:
            parts = line.split()
            if len(parts) >= 2:
                totals[parts[0]] = totals.get(parts[0], 0.0) + float(parts[-1])
            continue
        value = rest.rsplit("}", 1)[-1].strip().split()[-1]
        totals[name] = totals.get(name, 0.0) + float(value)
    return totals


def self_metric_totals(url: str) -> dict[str, float]:
    """Named totals from a Collector or sink self-metrics endpoint."""
    return parse_prometheus_text(read_text(url))


def wait_for_url(url: str, timeout_seconds: float = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 400:
                    return
        except (OSError, urllib.error.URLError) as caught:
            error = caught
        time.sleep(0.25)
    raise TimeoutError(f"{url} was not ready after {timeout_seconds}s: {error}")


def query_result(base_url: str, query: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.urlencode({"query": query})
    payload = read_json(f"{base_url}/api/v1/query?{encoded}")
    return payload["data"]["result"]


def query_value(base_url: str, query: str) -> float | None:
    """The first sample of a query, or None when it returned nothing usable."""
    result = query_result(base_url, query)
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if isnan(value) else value


def query_scalar(base_url: str, query: str) -> float:
    value = query_value(base_url, query)
    return 0.0 if value is None else value


def metric_names(base_url: str) -> set[str]:
    return set(read_json(f"{base_url}/api/v1/label/__name__/values")["data"])


def series_names(base_url: str, job: str) -> set[str]:
    """Metric names one scrape job published, which `metric_names` cannot tell apart."""
    encoded = urllib.parse.urlencode({"match[]": f'{{job="{job}"}}'})
    payload = read_json(f"{base_url}/api/v1/series?{encoded}")
    return {series["__name__"] for series in payload["data"] if series.get("__name__")}


def resolve_name(available: set[str], name: str) -> str | None:
    """Counters arrive with or without the _total suffix depending on the reader."""
    for candidate in (name, f"{name}_total"):
        if candidate in available:
            return candidate
    return None


def counters(
    base_url: str,
    job: str,
    signals: Sequence[str],
    available: set[str] | None = None,
) -> dict[str, dict[str, float | None]]:
    """The four delivery counters per signal, for one scrape job."""
    target_up = query_value(base_url, f'up{{job="{job}"}}')
    if target_up != 1:
        return {signal: {role: None for role in COUNTER_ROLES} for signal in signals}
    names = series_names(base_url, job) if available is None else available
    result: dict[str, dict[str, float | None]] = {}
    for signal in signals:
        unit = SIGNAL_UNITS[signal]
        per_signal: dict[str, float | None] = {}
        for role, template in COUNTER_ROLES.items():
            resolved = resolve_name(available=names, name=template.format(unit=unit))
            if resolved is None:
                per_signal[role] = None
                continue
            per_signal[role] = query_value(
                base_url,
                f'sum({{__name__="{resolved}", job="{job}"}})',
            )
        result[signal] = per_signal
    return result


def counters_by_exporter(
    base_url: str,
    job: str,
    signal: str,
) -> dict[str, float]:
    """Sent items split by exporter, so debug and otlp are not summed together."""
    unit = SIGNAL_UNITS[signal]
    resolved = resolve_name(
        series_names(base_url, job),
        f"otelcol_exporter_sent_{unit}",
    )
    if resolved is None:
        return {}
    results = query_result(base_url, f'{{__name__="{resolved}", job="{job}"}}')
    return {
        result["metric"].get("exporter", "unknown"): float(result["value"][1])
        for result in results
    }


def series_count(base_url: str, family: str) -> int:
    return round(query_scalar(base_url, f'count({{__name__="{family}"}})'))


def families_series_count(base_url: str, families: Sequence[str]) -> dict[str, int]:
    return {family: series_count(base_url, family) for family in families}


def flatten(sample: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in sample.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat |= flatten(value, f"{path}.")
        elif value is not None:
            flat[path] = float(value)
    return flat


def wait_until_stable(
    sample: Callable[[], Mapping[str, Any]],
    stable_rounds: int = 3,
    poll_seconds: float = 1.0,
    timeout_seconds: float = 90.0,
    require_nonzero: bool = True,
    minimum_seconds: float = 0.0,
) -> Mapping[str, Any]:
    """Poll until the sample stops changing, instead of sleeping a guessed amount.

    `minimum_seconds` is the floor for accepting an all-zero sample: without it,
    a counter that has not been scraped yet is indistinguishable from one that
    will never exist.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    previous: dict[str, float] | None = None
    repeats = 0
    current: Mapping[str, Any] = {}
    while time.monotonic() < deadline:
        current = sample()
        flat = flatten(current)
        settled = flat == previous and (any(flat.values()) or not require_nonzero)
        repeats = repeats + 1 if settled else 0
        if repeats >= stable_rounds and time.monotonic() - started >= minimum_seconds:
            return current
        previous = flat
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"the sample did not settle within {timeout_seconds}s; "
        f"the last observation was {current}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("expression")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    print(json.dumps(query_result(args.url, args.expression), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
