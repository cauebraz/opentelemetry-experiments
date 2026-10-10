# L4-05 Scrape or remote write for native histograms?

Level 4, theme metrics. 4 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L4-05`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `scrape-healthy` | prometheus exporter, Prometheus up |
| `scrape-down` | prometheus exporter, Prometheus stopped during the load |
| `remote-write-healthy` | prometheusremotewrite RW1, Prometheus up |
| `remote-write-down` | prometheusremotewrite RW1, Prometheus stopped during the load |

## Measured

| Variant | Path | HTTP | Native | Native count | Classic count | Native sum | p95 | Series | Down s | During send_failed | During queue | During failed_tx | After send_failed | After queue | After failed_tx | Restored |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scrape-healthy` | scrape | 200 | True | 200 | - | 500.0 | 13.929 | 1 | 0.0 | - | - | - | - | 0 | - | True |
| `scrape-down` | scrape | 200 | True | 200 | - | 500.0 | 13.929 | 1 | 8.4 (8.4 to 8.4) | - | 0 | - | - | 0 | - | True |
| `remote-write-healthy` | remote-write | 200 | False | - | - | - | - | 0 | 0.0 | - | - | - | - | 0 | 1 | True |
| `remote-write-down` | remote-write | 200 | False | - | - | - | - | 0 | 8.4 (8.4 to 8.4) | - | 0 | - | - | 0 | 1 | True |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Scrape and remote write 1.0 did not treat the same Delta exponential
histogram as equals. With `scrape_native_histograms: true` and protobuf
negotiation, every healthy scrape landed one native series named
`l4_05_duration_milliseconds`, count 200, sum 500, and
`histogram_quantile(0.95)` answered 13.929. Stopping Prometheus for about
8.4 s during the load did not lose that answer: the exporter kept the last
value, `:8888` stayed readable (`queue_size` 0), and after Prometheus came
back the same native count and p95 were there. The scrape path does not
queue toward Prometheus; recovery is whatever the exporter still exposes.

Remote write 1.0 never produced a queryable series for that Delta input.
`otelcol_exporter_prometheusremotewrite_failed_translations` rose to 1 in
every RW run, the queue stayed empty, and `send_failed` never moved. The
exporter README warns that non-cumulative histograms are dropped; the
counter is the measured form of that drop. A Cumulative pilot outside this
session can deliver a native histogram over RW1, so the failure here is the
temporality contract, not an inability of Prometheus to store native data
from remote write. PRW 2.0 remains marked not ready on this pin and was not
measured as a peer of RW1.

## What this does not say

Measured with one Delta exponential histogram of 200 observations on
`otelcol-contrib 0.162.0` and Prometheus `v3.15.0` with
`--web.enable-remote-write-receiver` and `scrape_native_histograms: true`
on the exported job. Cumulative remote write, PRW 2.0, multi-tenant
backends, and scrape intervals other than 1 s are outside the run. Text
exposition of the prometheus exporter still shows a classic `+Inf` view;
native shape is visible only after a protobuf scrape that accepts native
histograms. Docker Desktop on Darwin arm64 is the host for these numbers.
