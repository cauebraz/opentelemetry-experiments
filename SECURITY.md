# Security

Report vulnerabilities privately through GitHub Security Advisories on this
repository:

https://github.com/cauebraz/opentelemetry-experiments/security/advisories/new

Do not open a public issue for a vulnerability until a fix or advisory is
ready.

## Scope

This project runs a local Docker Compose stack for recorded experiments. It
does not accept network traffic outside `127.0.0.1` by default. Please include:

- the question or recipe that reproduces the issue;
- Collector, Prometheus and Python versions from `docs/METHOD.md` pins;
- whether the stack was started with `just up`, `just ask` or `just smoke`.

## Non-security issues

Config bugs, harness failures and documentation gaps belong in ordinary
GitHub issues.
