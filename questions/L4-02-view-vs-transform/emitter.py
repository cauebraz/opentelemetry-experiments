"""Emit the L4-02 histogram workload through the real Python metrics SDK."""

import json
import os
import resource
import sys
import time
from typing import Any

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

from opentelemetry_experiments.core import stack

METRIC = "http.server.duration"
UNIT = "ms"
USERS = 200
OBSERVATIONS_PER_USER = 3
METHODS = ("GET", "POST", "PUT", "DELETE")
STATUSES = ("200", "201", "400", "404", "500")
BOUNDS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 10.0)
KEPT = frozenset({"environment", "region", "http.method", "http.status_code"})
SERVICE = {"service.name": "l4-02"}


class CountingExporter(OTLPMetricExporter):
    """Records protobuf body bytes before the parent sends each batch."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request_bytes = 0
        self.exports = 0

    def _export_batch(self, export_request: Any) -> Any:
        self.request_bytes += len(export_request.SerializeToString())
        self.exports += 1
        return super()._export_batch(export_request)


def observations() -> list[dict[str, Any]]:
    """200 users, three observations each, fixed method and status per user.

    Method and status vary across users so that dropping `user.id` leaves the
    twenty (method, status) identities the reductions are expected to keep.
    """
    rows = []
    for user in range(USERS):
        method = METHODS[user % len(METHODS)]
        status = STATUSES[user % len(STATUSES)]
        for offset in range(OBSERVATIONS_PER_USER):
            index = user * OBSERVATIONS_PER_USER + offset
            rows.append(
                {
                    "user.id": f"user-{user:03d}",
                    "environment": "prod",
                    "region": "us-east",
                    "http.method": method,
                    "http.status_code": status,
                    "value": float((index % 7) + 1),
                }
            )
    return rows


def build_provider(use_view: bool) -> tuple[MeterProvider, CountingExporter]:
    os.environ["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "DELTA"
    exporter = CountingExporter(
        endpoint=f"{stack.COLLECTOR_HTTP_URL}/v1/metrics",
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=60_000,
        export_timeout_millis=30_000,
    )
    views = [
        View(
            instrument_name=METRIC,
            aggregation=ExplicitBucketHistogramAggregation(boundaries=list(BOUNDS)),
            attribute_keys=set(KEPT) if use_view else None,
        )
    ]
    provider = MeterProvider(
        metric_readers=[reader],
        resource=Resource.create(SERVICE),
        views=views,
    )
    return provider, exporter


def emit(use_view: bool) -> dict[str, Any]:
    provider, exporter = build_provider(use_view)
    meter = provider.get_meter("l4-02")
    histogram = meter.create_histogram(METRIC, unit=UNIT)
    rows = observations()
    started = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    for row in rows:
        value = row["value"]
        attributes = {key: row[key] for key in row if key != "value"}
        histogram.record(value, attributes)
    flushed = provider.force_flush(timeout_millis=30_000)
    provider.shutdown()
    elapsed = time.monotonic() - started
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (usage_after.ru_utime - usage_before.ru_utime) + (
        usage_after.ru_stime - usage_before.ru_stime
    )
    return {
        "observations": len(rows),
        "users": USERS,
        "use_view": use_view,
        "flushed": flushed,
        "exports": exporter.exports,
        "request_bytes": exporter.request_bytes,
        "sdk_wall_s": round(elapsed, 3),
        "sdk_cpu_s": round(cpu, 3),
    }


def main() -> int:
    options = json.loads(sys.argv[1])
    print(json.dumps(emit(bool(options["use_view"]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
