Configure exporter queue and retry settings (for example, `retry_on_failure` or
`sending_queue` settings) on agents and gateways to handle temporary outages
between agents and gateways or between gateways and backends. Gateways often
need larger queues and retry policies to handle backend outages.

# Provenance

Source: https://github.com/open-telemetry/opentelemetry.io/blob/a12d86209bb807217742cc14e0a29095beaf978f/content/en/docs/collector/deploy/other/agent-to-gateway/_index.md
Commit: a12d86209bb807217742cc14e0a29095beaf978f
Lines: 422-425 under the Retries heading.
