# L4-01 What double counts, and what goes invisible, in an agent plus gateway topology?

Level 4, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L4-01`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `healthy` | all three layers reachable |
| `gateway-paused` | gateway paused between burst and recovery |
| `sink-paused` | sink paused between burst and recovery |

## Measured

| Variant | HTTP | Agent accepted | Agent sent | Agent send_failed | Gateway accepted | Gateway sent | Sink accepted | Double-count sum | During agent queue | During gateway queue | Drain s | Restored | Sink log spans |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `healthy` | 200 | 1000 | 1000 | - | 1000 | 1000 | 1000 | 2000 | 0 | 0 | 2.0 | True | 1000 |
| `gateway-paused` | 200 | 1000 | 1000 | - | 1000 | 1000 | 1000 | 2000 | 1 | - | 3.0 (3.0 to 3.0) | True | 1000 |
| `sink-paused` | 200 | 1000 | 1000 | - | 1000 | 1000 | 1000 | 2000 | 0 | 1 | 3.0 (2.0 to 3.0) | True | 1000 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

Every span crossed two collectors before the sink. On the healthy path the
agent accepted 1000, sent 1000, the gateway accepted 1000 and sent 1000, and
the sink accepted 1000. Adding agent sent to gateway accepted produced 2000:
the same 1000 items counted once per hop. A dashboard that sums those two
jobs double-counts delivery.

Pausing the gateway left the sender at HTTP 200 and raised the agent queue
to 1 while `send_failed` stayed empty; after unpause the sink reached 1000
in about 3 s. Pausing the sink left the agent queue at 0 and raised the
gateway queue to 1; the agent layer alone could not show the sink outage.
Queues drained to empty in every recovery. The number that answers "did it
land" is the sink's accepted counter, not a sum across layers.

## What this does not say

Measured with one agent, one gateway and one sink on
`otelcol-contrib 0.162.0`, 1000 spans, batch size 256, and the same queue
and retry settings on both hops. Load-balanced gateways, tail sampling,
TLS between tiers, and pause durations other than 8 s are outside the run.
Docker Desktop on Darwin arm64 is the host.
