---
id: L4-01
title: What double counts, and what goes invisible, in an agent plus gateway topology?
level: 4
theme: collector
status: measured
---

## Question

A collector per host feeding a central collector is the common production shape.
It also means every delivery counter exists twice, and a failure in one layer
can be invisible in the other. This question measures which numbers can be
summed across layers and which cannot.

## What the documentation says

> Configure exporter queue and retry settings (for example, `retry_on_failure`
> or `sending_queue` settings) on agents and gateways to handle temporary
> outages between agents and gateways or between gateways and backends.
> Gateways often need larger queues and retry policies to handle backend
> outages.

Source: [Agent-to-gateway deployment pattern](https://github.com/open-telemetry/opentelemetry.io/blob/a12d86209bb807217742cc14e0a29095beaf978f/content/en/docs/collector/deploy/other/agent-to-gateway/_index.md?plain=1#L422-L425),
commit `a12d86209bb807217742cc14e0a29095beaf978f`. Local copy:
[`vendor/agent-to-gateway-retries.md`](vendor/agent-to-gateway-retries.md).

```bash
curl -sL https://raw.githubusercontent.com/open-telemetry/opentelemetry.io/a12d86209bb807217742cc14e0a29095beaf978f/content/en/docs/collector/deploy/other/agent-to-gateway/_index.md | sed -n '422,425p'
```

## Hypothesis

One thousand deterministic spans, unique trace and span IDs, travel
agent→gateway→sink with batch, sending_queue and retry_on_failure enabled on
both hops. Three variants:

1. `healthy`: every layer's accepted and sent counters rise by 1000; the sink
   receives 1000; summing agent sent with gateway accepted double-counts the
   same items.
2. `gateway-paused`: the gateway is paused between the burst and recovery.
   The sender still gets HTTP 200 because the agent queues; agent
   `send_failed` stays near zero while queue size is non-zero; gateway and
   sink accepted stay flat until unpause, then the sink reaches 1000.
3. `sink-paused`: the sink is paused. Agent and gateway accepted/sent rise;
   gateway queue holds the batch; sink accepted stays flat until unpause, then
   reaches 1000. The agent layer alone cannot show the sink outage.

Invalid when spans are lost or duplicated at the sink after recovery, when a
queue remains non-empty after drain, when counter and log disagree on delivery,
when a process restarts or is OOM-killed, or when a paused layer is not
restored. Three repetitions with variants rotated. An expected outage is a
result, not an invalid run.

## How to run

```bash
just ask L4-01
```

Pins: `otelcol-contrib 0.162.0`. Topology uses the compose `gateway` profile.

## What is measured

| Field | Meaning |
| --- | --- |
| http_status | response to the sender at the agent |
| agent / gateway / sink accepted | `otelcol_receiver_accepted_spans` per job |
| agent / gateway sent and send_failed | exporter counters per job |
| during_queue | exporter queue size while the layer is paused |
| sink_delivered | spans accepted at the sink after recovery |
| drain_s | seconds from unpause until the sink reaches 1000 |
| double_count_sum | agent sent + gateway accepted (same items twice when healthy) |

## Result

9 runs, 0 invalid. Healthy delivery was 1000 at every layer; agent sent plus
gateway accepted summed to 2000 for the same 1000 spans. Gateway pause queued
at the agent (queue 1, HTTP 200); sink pause queued at the gateway (queue 1)
while the agent stayed empty. After recovery the sink always reached 1000 and
queues drained. See [`results/REPORT.md`](results/REPORT.md).

## What this does not say

Load-balanced gateways, tail sampling and TLS between tiers are outside the
run.
