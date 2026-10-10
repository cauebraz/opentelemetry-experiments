# L1-02 Does the documented per-level metric list match what the Collector emits?

Level 1, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Rendered from this question's `results.json` by `just render L1-02`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `level-none` | service.telemetry.metrics.level set to none |
| `level-basic` | service.telemetry.metrics.level set to basic |
| `level-normal` | service.telemetry.metrics.level set to normal |
| `level-detailed` | service.telemetry.metrics.level set to detailed |

## Measured

| Variant | HTTP status | Metric families published | Documented at level | Of those, present | Of those, missing | Undocumented present | Batch metrics of 4 | Matched without unit | Accepted points | Published names | Missing names |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `level-none` | 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - | none | none |
| `level-basic` | 200 | 19 | 28 | 16 | 12 | 3 | 0 | 0 | 100 | otelcol_exporter_in_flight_requests, otelcol_exporter_sent_log_records, otelc... | otelcol_exporter_enqueue_failed_log_records, otelcol_exporter_enqueue_failed_... |
| `level-normal` | 200 | 23 | 32 | 20 | 12 | 3 | 4 | 0 | 100 | otelcol_exporter_in_flight_requests, otelcol_exporter_sent_log_records, otelc... | otelcol_exporter_enqueue_failed_log_records, otelcol_exporter_enqueue_failed_... |
| `level-detailed` | 200 | 28 | 42 | 25 | 17 | 3 | 4 | 0 | 100 | http_server_request_body_size, http_server_request_duration, http_server_resp... | http.client.request.body.size, http.client.request.duration, otelcol_exporter... |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The table is not a checklist. At `basic` the documentation lists 28 names and
19 metric families exist, and only 16 of those 19 are on the list. At `detailed` it
lists 42 and 25 of them exist. Every absence has a reason, and the reasons are
not the level.

Seven of the twelve names absent at `basic` belong to components that are not
there: `otelcol_scraper_*` needs a scraping receiver, and the three
`otelcol_exporter_enqueue_failed_*` names plus
`otelcol_exporter_queue_capacity` and `otelcol_exporter_queue_size` need
`sending_queue`. Two more, `otelcol_processor_incoming_items` and
`otelcol_processor_outgoing_items`, are absent even though the pipeline has a
`batch` processor: they are declared in `processorhelper`, and
`batchprocessor` records through its own telemetry builder instead. A reader
of the table has no way to know that.

The remaining three are the ones that matter on a dashboard.
`otelcol_exporter_send_failed_log_records`, `_metric_points` and `_spans` do
not exist on a run where nothing failed, while
`otelcol_receiver_refused_spans` and its siblings do exist and read zero on
the same run. Both are counters at `basic`, both were listed, and one is a
flat zero line while the other is "no data". The difference is whether the
component records a zero, not whether the level includes the metric. This is
L0-01's finding one layer up: a panel on `send_failed` is empty on a healthy
collector and empty on a collector whose metric was never registered, and the
two look identical.

Going from `basic` to `normal` adds exactly the four `otelcol_processor_batch_*`
names the table says it adds, which is the one place the page is precise and
correct. Going to `detailed` adds five of its ten: the three `http.server.*`
families, `rpc.client.call.duration` and
`otelcol_processor_batch_batch_send_size_bytes`. The five it does not add are
the mirror image of the traffic, `http.client.*` for an HTTP export that never
happens and `rpc.server.call.duration` for a gRPC request that never arrives,
plus the two queue sizes.

Three names appear at every enabled level and in no table:
`otelcol_receiver_failed_spans`, `_metric_points` and `_log_records`. They are
declared in `receiverhelper` at this version, described as items that "failed
to be processed by the receiver due to internal errors", and the page's lists
do not mention them. An operator reading only the documentation does not know
the metric exists.

Two predictions failed. The author expected the pull reader's
`without_units: true` to shorten the unit-carrying names, so that
`otelcol_process_cpu_seconds` would arrive as `otelcol_process_cpu`;
`Matched without unit` reads 0 at every level, and the names arrive exactly as
the table spells them. The author also expected no undocumented `otelcol_*`
name below `detailed`, and three appear at `basic`.

## What this does not say

Measured on `otelcol-contrib 0.162.0` against the page at commit
`fb0a807365b1`; both move, and the comparison is only valid for that pair. The
absences are properties of this configuration as much as of the level: a
pipeline with `sending_queue` enabled, a scraping receiver, or a processor
built on `processorhelper` would publish names that are missing here. Nothing
is said about the series count, only about the metric names: `detailed`
"includes dimensions and views", and a name that exists at two levels can
carry many more label combinations at the higher one, which is the cost the
page warns about and this question does not measure. The histogram families
are counted once each, so the `_bucket`, `_sum` and `_count` series they
expand into are not in `Metric families published`.
