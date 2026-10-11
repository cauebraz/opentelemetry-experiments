# Learnings

One sentence per measured fact, grouped by theme. A sentence enters this file
only with a link to the `results.json` that produced it. Nothing here is an
opinion, a recommendation, or a restatement of the documentation.

## Collector

- Summing `otelcol_exporter_sent_metric_points` without an `exporter` label
  counts fan-out, not volume: 100 datapoints through a pipeline with three
  exporters reads 300, and 200 once one exporter is removed
  ([L0-03](questions/L0-03-where-the-collector-tells-you/results/results.json)).
- The `healthcheckextension` endpoint returned 200 while the collector was
  refusing every datapoint it received and delivering none of them downstream
  ([L0-03](questions/L0-03-where-the-collector-tells-you/results/results.json)).
- With `sending_queue` and `retry_on_failure` disabled, an unreachable OTLP
  destination makes the OTLP receiver answer 503 and count the batch as
  refused rather than accepted, so the failure reaches the sender
  ([L0-03](questions/L0-03-where-the-collector-tells-you/results/results.json)).
- At `service.telemetry.metrics.level: none` the collector published no
  `otelcol_*` series of its own while still delivering all 100 datapoints
  downstream
  ([L0-03](questions/L0-03-where-the-collector-tells-you/results/results.json)).
- Of the 28 metric names the documentation lists at `basic`, 16 existed on a
  collector with an OTLP receiver, a `batch` processor and three exporters; at
  `detailed`, 25 of the listed 42
  ([L1-02](questions/L1-02-telemetry-level/results/results.json)).
- `otelcol_exporter_send_failed_*` does not exist until an export fails, while
  `otelcol_receiver_refused_*` exists and reads zero from the first request,
  so two counters the documentation lists at the same level behave differently
  on a healthy collector
  ([L1-02](questions/L1-02-telemetry-level/results/results.json)).
- `otelcol_processor_incoming_items` and `otelcol_processor_outgoing_items`
  were absent from a pipeline whose only processor was `batch`, at every
  telemetry level
  ([L1-02](questions/L1-02-telemetry-level/results/results.json)).
- `otelcol_receiver_failed_spans`, `_metric_points` and `_log_records` were
  published at `basic` and appear in none of the three per-level tables in the
  Collector's internal telemetry documentation
  ([L1-02](questions/L1-02-telemetry-level/results/results.json)).
- Moving from `basic` to `normal` added exactly the four
  `otelcol_processor_batch_*` names the documentation says it adds
  ([L1-02](questions/L1-02-telemetry-level/results/results.json)).
- Two trace pipelines on one OTLP receiver are isolated: an `attributes`
  processor inserting a key in one pipeline left no trace in the other, and
  when both inserted, each side carried only its own value
  ([L1-03](questions/L1-03-env-vars-and-two-pipelines/results/results.json)).
- One span accepted once was counted twice by `otelcol_exporter_sent_spans`
  when two pipelines consumed it, so the counter multiplies by pipelines as
  well as by exporters
  ([L1-03](questions/L1-03-env-vars-and-two-pipelines/results/results.json)).
- With `sending_queue` and `retry_on_failure` at their defaults, a burst sent
  while the destination was unreachable was answered with HTTP 200 and counted
  as accepted, the same as a delivered burst
  ([L2-01](questions/L2-01-backend-down/results/results.json)).
- `otelcol_exporter_send_failed_metric_points` was absent throughout a
  20 second outage in which the sink stayed unavailable and no successful
  sink export was recorded, while the collector log carried three to four
  `Exporting failed` lines at `info` level
  ([L2-01](questions/L2-01-backend-down/results/results.json)).
- A paused destination failed each export on the exporter's 5s timeout with
  `DeadlineExceeded` and a stopped one failed at once with `Unavailable`, and
  both delivered all 100 datapoints after recovery, with the paused sink
  stabilizing sooner in all three repetitions
  ([L2-01](questions/L2-01-backend-down/results/results.json)).
- `otelcol_exporter_queue_capacity` read 1000 and one burst of 100 datapoints
  moved `otelcol_exporter_queue_size` to 1, because the default `sizer` counts
  requests
  ([L2-01](questions/L2-01-backend-down/results/results.json)).
- After the default retry budget ended, a request the receiver had answered
  with HTTP 200 became exactly 100
  `otelcol_exporter_send_failed_metric_points`, while the receiver still
  reported 100 accepted and 0 refused
  ([L2-07](questions/L2-07-retry-exhausted/results/results.json)).
- Retry exhaustion moved `otelcol_exporter_queue_size` from 1 to 0 and emitted
  one `error` log with `Dropping data.`, `no more retries left` and
  `dropped_items: 100` in every repetition
  ([L2-07](questions/L2-07-retry-exhausted/results/results.json)).
- With `max_elapsed_time` at its default of 300 seconds, the first non-zero
  `send_failed` sample appeared after 265, 281 and 300 seconds, so the setting
  was an upper bound rather than a minimum retry duration in these runs
  ([L2-07](questions/L2-07-retry-exhausted/results/results.json)).
- Across five batch configurations, exactly one of
  `otelcol_processor_batch_batch_size_trigger_send` and
  `otelcol_processor_batch_timeout_trigger_send` was published in each and the
  other had no series, and their sum equalled
  `otelcol_processor_batch_batch_send_size_count`
  ([L2-03](questions/L2-03-batch-sizing/results/results.json)).
- The same 100 datapoints, arriving one per request over 5.6 seconds, produced
  100 export requests at `send_batch_size: 1`, 20 at 5, and 1 or 2 at 8192
  with `timeout: 10s`
  ([L2-03](questions/L2-03-batch-sizing/results/results.json)).
- With 0, 3 and 7 seconds idle before the first item, the 10 second timeout
  completed after 11, 8 and 14 seconds and fired once, once and twice, so it
  runs on a process-relative ticker instead of starting with the first item
  ([L2-03](questions/L2-03-batch-sizing/results/results.json)).
- `otelcol_processor_batch_batch_send_size_bytes_sum` read 14500 whether the
  100 datapoints were sent as 100 batches or as 1, so it sums item bytes and
  not request bytes
  ([L2-03](questions/L2-03-batch-sizing/results/results.json)).
- With `batch` before `memory_limiter`, all 200 requests were answered with
  HTTP 200 and 1130 to 1140 of the 2000 records never arrived, while
  `otelcol_receiver_refused_log_records` read 0
  ([L2-02](questions/L2-02-memory-limiter-placement/results/results.json)).
- With `memory_limiter` first, the request after the heap crossed the soft
  limit was answered with HTTP 503 and every record answered with 200 arrived
  after recovery
  ([L2-02](questions/L2-02-memory-limiter-placement/results/results.json)).
- Behind `batch`, `otelcol_processor_memory_limiter_refused_log_records`
  equalled the acknowledged records that never arrived in each run, 1140, 1130
  and 1140, while the batch processor's `Sender failed` warning appeared exactly
  10 times per run with no item count
  ([L2-02](questions/L2-02-memory-limiter-placement/results/results.json)).
- With `batch` first the heap peaked at 104.9 to 111.9 MiB against a 96 MiB
  `limit_mib`, and with `memory_limiter` first at 84.9 to 90.2 MiB
  ([L2-02](questions/L2-02-memory-limiter-placement/results/results.json)).

- Without compression, OTLP/gRPC and OTLP/HTTP protobuf delivered 100000
  points of one metric as 7338654 and 7339793 bytes at the sink's interface,
  73.4 bytes per point and 0.02% apart
  ([L3-06](questions/L3-06-compression-and-protocol/results/results.json)).
- gzip cut both protocols to 8.4 bytes per point, 88.5% less, and raised
  idle-adjusted Collector CPU per point from 1802.7 to 2150.2 ns on gRPC and
  from 1825.9 to 2245.3 ns on HTTP, 19.3% and 23.0%, across four balanced
  execution orders
  ([L3-06](questions/L3-06-compression-and-protocol/results/results.json)).
- With Prometheus stopped and nothing sent, the sink's `eth0` still received
  639 to 934 bytes per workload window
  ([L3-06](questions/L3-06-compression-and-protocol/results/results.json)).

- With the in-memory queue, a SIGKILL of the Collector lost all 100 log
  records the receiver had answered with HTTP 200, with no error line in the
  log and no `accepted`, `sent` or `send_failed` logs series on the restarted
  Collector
  ([L2-08](questions/L2-08-persistent-queue/results/results.json)).
- A `file_storage` persistent queue on a volume delivered 100 of 100 after
  both SIGTERM and SIGKILL, with 0 duplicates, and the restarted Collector read
  10 requests in `otelcol_exporter_queue_size` before the destination returned
  ([L2-08](questions/L2-08-persistent-queue/results/results.json)).
- A SIGTERM shutdown wrote 10 `Exporting failed. Dropping data.` error lines
  summing `dropped_items` to 100 for both the memory and the persistent queue,
  and the persistent queue then delivered all 100
  ([L2-08](questions/L2-08-persistent-queue/results/results.json)).
- The same persistent config with the container recreated on a new volume
  delivered 0 of 100 and logged no error
  ([L2-08](questions/L2-08-persistent-queue/results/results.json)).

- `otelcol validate` exited 0 with no output for an exporter configured and
  in no pipeline, and for a receiver endpoint set to an undefined
  `${env:...}`, exactly as for the minimal config
  ([L1-01](questions/L1-01-pipeline-from-zero/results/results.json)).
- Seven refused configs each exited 1 and named the pipeline, component or key
  at fault; only the YAML error carried a line number, and it pointed at the
  start of the enclosing block, six lines above the mis-indented key
  ([L1-01](questions/L1-01-pipeline-from-zero/results/results.json)).
- An unknown exporter type listed the same 57 valid types in a different order
  on every run, with no suggestion
  ([L1-01](questions/L1-01-pipeline-from-zero/results/results.json)).
- At the receiver, uncompressed gRPC and HTTP protobuf cost 74.0 and 73.9
  bytes per point, gzip cut both by 88%, and HTTP JSON cost 218.5, 2.96 times
  protobuf
  ([L1-04](questions/L1-04-grpc-vs-http-receiver/results/results.json)).
- HTTP JSON cost the Collector 1586.7 ns per point against 836.5 for HTTP
  protobuf, and gzip added 32% to 38% to the Collector's CPU per point
  ([L1-04](questions/L1-04-grpc-vs-http-receiver/results/results.json)).
- All 25 OTLP/HTTP JSON requests sent to the gRPC port failed with
  `BadStatusLine`, although the receiver README says the address is the same
  ([L1-04](questions/L1-04-grpc-vs-http-receiver/results/results.json)).

## Traces

- A `trace_id` of 15 bytes is rejected by the OTLP/HTTP JSON decoder with 400
  and `ID.UnmarshalJSONIter: length mismatch`, and neither
  `otelcol_receiver_accepted_spans` nor `otelcol_receiver_refused_spans`
  exists for that run, so no Collector counter records the loss
  ([L0-01](questions/L0-01-span-on-the-wire/results/results.json)).
- A span with no `endTimeUnixNano` is accepted with no warning and rendered
  with its end time at `1970-01-01 00:00:00 +0000 UTC`
  ([L0-01](questions/L0-01-span-on-the-wire/results/results.json)).
- A `resourceSpans` entry with no `resource` is accepted with no warning,
  printed by the `debug` exporter with zero resource attributes, and counted
  as one span by the next collector
  ([L0-01](questions/L0-01-span-on-the-wire/results/results.json)).
- With `retry_on_failure` disabled and spans of each trace spread across a
  20 second outage, all 20 traces arrived at the destination and none was
  complete: 11 to 16 held 2 of their 5 spans and 4 to 9 held 3, with 0 traces
  missing
  ([L2-05](questions/L2-05-traces-under-exporter-failure/results/results.json)).
- All 20 root spans arrived in every variant, including the two that lost
  spans, because the root was sent before the outage began
  ([L2-05](questions/L2-05-traces-under-exporter-failure/results/results.json)).
- With the default retry, 48 to 50 requests were queued immediately before
  recovery and all 100 spans arrived 20.4 to 26.4 seconds after restart, with
  both the cumulative sink counter and the unsampled sink log reading 100
  ([L2-05](questions/L2-05-traces-under-exporter-failure/results/results.json)).
- `otelcol_exporter_queue_size` peaked at 0 with retry disabled while 51 to 56
  spans were dropped, because a request that is never retried never dwells in
  the queue
  ([L2-05](questions/L2-05-traces-under-exporter-failure/results/results.json)).
- Shortening `max_elapsed_time` to 10 seconds recovered 78 to 80 of 100 spans
  and produced only 0 or 1 complete trace, while the queue gauge read 30 to 32
  requests immediately before recovery
  ([L2-05](questions/L2-05-traces-under-exporter-failure/results/results.json)).
- `ParentBased(TraceIdRatioBased(0.10))` kept 12 of 100 error traces in 1000,
  while `tail_sampling` with `status_code: ERROR` kept all 100 at the same 10%
  of stored spans
  ([L3-03](questions/L3-03-head-vs-tail-sampling/results/results.json)).
- An unadjusted count of sampled spans read error rates of 12.2% after head
  sampling, 100% after an error-only tail policy and 50% after errors plus 10%,
  against 10% in the traffic
  ([L3-03](questions/L3-03-head-vs-tail-sampling/results/results.json)).
- Buffering 1000 traces of 5 spans for a 5 second `decision_wait` raised the
  Collector's peak heap from 30.7-32.4 MiB to 32.2-36.3 MiB, with every trace
  decided and none dropped early
  ([L3-03](questions/L3-03-head-vs-tail-sampling/results/results.json)).

## Metrics

- An unbounded attribute on a Delta Sum produced one series per value, 10000
  for 10000, and the Cardinality Guardian's enforcing modes held it at 24,
  while cumulative sums, histograms and summaries stayed at their input count
  ([L3-01](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/benchmark/results.json)).
- Removing `user.id` from a histogram with an SDK view or an aggregating
  transform cut 2200 series to 220 and kept all 600 requests, while deleting
  the key in a transform kept 60 of 600
  ([L3-02](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/histograms/results.json)).
- An SDK View with `attribute_keys` and a Collector
  `aggregate_on_attributes` both cut the same histogram from 200 identities
  to 20 and kept all 600 observations and the same per-method p95; only the
  View also cut the SDK request from 63054 bytes to 6070 and made `user.id`
  invisible to the platform, while the transform's inbound count still saw
  all 200 values
  ([L4-02](questions/L4-02-view-vs-transform/results/results.json)).
- Schema `hard-rename` of `http.status_code` made live spans invisible to an
  old-only query while dual-write kept both names on every live span at 4.0
  attributes each and 16838 batch item bytes against 14336 for hard rename;
  historical spans in the sink log stayed on the old name either way
  ([L4-03](questions/L4-03-semconv-migration/results/results.json)).
- Exponential histograms scraped as native histograms kept 200 identities in
  200 series and still answered a per-user p95
  ([L3-02](https://github.com/cauebraz/opentelemetry-cardinality-lab/blob/ad5779d31d33c876f84d5e2bf164d427b89b356e/results/histograms/results.json)).
- A Delta exponential histogram scraped with `scrape_native_histograms: true`
  answered count 200, sum 500 and p95 13.929 as one native series, including
  after an 8.4 s Prometheus outage; the same Delta input over remote write 1.0
  produced zero series and raised `failed_translations` to 1
  ([L4-05](questions/L4-05-remote-write-vs-scrape/results/results.json)).
- Each `spanmetrics` dimension combination cost exactly 10 Prometheus series
  with six explicit buckets, so a unique-per-request `customer.id` turned 1000
  spans into 10000 series against 100 for the route alone
  ([L3-05](questions/L3-05-spanmetrics-connector/results/results.json)).
- `spanmetrics` after 10% head sampling read an error rate of 5.8% and a mean
  duration 19.8% low against the traffic, and after an error-only tail policy an
  error rate of 100%
  ([L3-05](questions/L3-05-spanmetrics-connector/results/results.json)).
- With every span kept, `histogram_quantile(0.95)` over buckets ending at
  `500ms` and `1s` returned 750 ms for traffic whose true p95 was 1000 ms
  ([L3-05](questions/L3-05-spanmetrics-connector/results/results.json)).

- A `Sum` with no `aggregationTemporality` is accepted by the OTLP receiver,
  counted as delivered by both the receiver and the exporter counters, reaches
  the next collector, and then produces zero series on the `prometheus`
  exporter with nothing logged
  ([L0-02](questions/L0-02-metric-and-log-on-the-wire/results/results.json)).

## Collector topology

- In an agent→gateway→sink path, 1000 spans produced accepted and sent of
  1000 at every layer, while agent sent plus gateway accepted summed to 2000
  for the same items; pausing the gateway queued at the agent and pausing the
  sink queued at the gateway, and only the sink accepted counter answered
  whether the batch landed
  ([L4-01](questions/L4-01-agent-vs-gateway/results/results.json)).
- A custom Collector built with `ocb v0.162.0` delivered the same 1000 logs
  as `otelcol-contrib`, stamped `experiment.custom_processor=true` on all of
  them only when that processor was enabled, and was refused by the stock
  binary (`validate` exit 1); the custom binary was 31719586 bytes and its
  idle RSS was about 12 MB against about 44 MB for contrib
  ([L4-04](questions/L4-04-custom-processor-with-ocb/results/results.json)).

## Logs

- A log record with no `timeUnixNano` is accepted with no warning, printed by
  the `debug` exporter with its timestamp at the Unix epoch, and counted as
  one record by the next collector
  ([L0-02](questions/L0-02-metric-and-log-on-the-wire/results/results.json)).
- A log record's `traceId` is printed by the `debug` exporter as the same 32
  hex characters that were sent, and the record is counted by the next
  collector; what the next hop printed was not read
  ([L0-02](questions/L0-02-metric-and-log-on-the-wire/results/results.json)).
- With a `regex_parser` on `on_error: drop`, ten lines in a hundred that fail
  the pattern leave no trace in any counter: accepted read 90,
  `otelcol_receiver_refused_log_records` and
  `otelcol_receiver_failed_log_records` both read 0, and no published series
  name contained `parser`, `operator`, `stanza` or `unparsed`
  ([L2-04](questions/L2-04-filelog-unparsed-lines/results/results.json)).
- `on_error` left unset behaved as `send`, delivering all 100 records with the
  ten unparsable lines kept as raw bodies carrying no parsed attributes and no
  parsed timestamp, so the sink printed the Unix epoch, and the collector log
  named the choice in its `"action": "send"` field
  ([L2-04](questions/L2-04-filelog-unparsed-lines/results/results.json)).
- `on_error: drop_quiet` discarded ten records with zero error lines, zero
  warn lines and no counter moving, because the failures are logged at `debug`
  ([L2-04](questions/L2-04-filelog-unparsed-lines/results/results.json)).
- Each loud run carried ten `Failed to process entry` lines plus exactly one
  `failed to emit token` line whose `error` field concatenated all ten parse
  errors, so counting error lines does not count failures
  ([L2-04](questions/L2-04-filelog-unparsed-lines/results/results.json)).
- 1000 records with typed attributes made a 440381 byte OTLP/HTTP JSON
  request against 274381 for the same events as text, 60.5% more, while the
  batch processor counted 9.7% fewer item bytes for them after the text was
  parsed
  ([L3-04](questions/L3-04-structured-vs-text-logs/results/results.json)).
- With 10 of 1000 text records in a second format, a count by route and outcome
  put those 10 under its `default_value` series while the control count, the
  receiver and the exporter counters all read 1000 and the Collector logged no
  warning
  ([L3-04](questions/L3-04-structured-vs-text-logs/results/results.json)).

## SDK

- With the Python `SimpleSpanProcessor` and the Collector stopped, each
  `span.end()` blocked the calling thread for 7.55 to 7.70 seconds and the span
  was lost, with no exception raised to the caller
  ([L2-06](questions/L2-06-sdk-without-collector/results/results.json)).
- With the Python batch processor at its defaults, 100 spans ended while the
  Collector was stopped all arrived when it was healthy again 0.42 to 0.43
  seconds later, with no SDK warning logged
  ([L2-06](questions/L2-06-sdk-without-collector/results/results.json)).
- A 20 second outage lost the 50 spans due at the first scheduled export, after
  three retries and one `Failed to export spans batch` error, while
  `span.end()` stayed under 0.1 ms
  ([L2-06](questions/L2-06-sdk-without-collector/results/results.json)).
- A burst of 200 spans into a 50-slot queue delivered only spans 150-199 in
  every run, so overflow kept the newest and dropped the oldest, with one
  `Queue full, dropping Span.` warning for 150 drops
  ([L2-06](questions/L2-06-sdk-without-collector/results/results.json)).
- A burst of 5000 spans in 0.04 seconds through the default 2048-span queue
  reached the Collector as 2560 spans, so the SDK dropped half before any
  sampler saw them; this was the invalid first run of L3-03, recorded in
  [its QUESTION.md](questions/L3-03-head-vs-tail-sampling/QUESTION.md)
  and not in `results.json`.

## Semantic conventions
