# Deployment and Infrastructure Guidance

## Scope

This file governs `deploy/`. The root guidance explicitly points here for
operational details when root `compose*.yaml`, `Dockerfile`, or deployment
scripts are changed with this subtree. It inherits
[repository guidance](../AGENTS.md); it does not override the parent scope for
files outside `deploy/`.

## Role in the System

Deployment configuration assembles PostgreSQL, migration, API, worker, isolated
parser, HTTPS edge, optional Ollama, and optional SearXNG plus Squid egress
restriction. It also defines ARM64, Jetson, inference-test, development, and CI
smoke overlays.

## Key Files and Entry Points

- `../compose.yaml`: authoritative base services, networks, volumes, secrets,
  health checks, limits, and profiles.
- `../compose.dev.yaml`, `../compose.override.yaml`: development/runtime
  refinements.
- `../compose.arm64.yaml`, `../compose.jetson.yaml`: inference platform overlays.
- `../compose.inference-test.yaml`, `../compose.ci-smoke.yaml`: test/mocked
  runtime overlays.
- `../Dockerfile`: shared Python image.
- `caddy/Caddyfile`, `caddy/Dockerfile`, `caddy/check.sh`: TLS ingress.
- `searxng/settings.yml`, `searxng/proxy_probe.py`, `searxng/check.sh`: search
  configuration and probe.
- `egress-proxy/squid.conf`, `egress-proxy/check.sh`: restricted search egress.
- `../scripts/check_deployment_contract.py`,
  `../scripts/deployment_smoke.sh`, `../scripts/runtime_smoke.py`,
  `../scripts/https_chat_stream_smoke.py`: validation.
- `../scripts/rotate-credentials.sh`, `../scripts/reset-deployment.sh`,
  `../scripts/mongars-status.sh`: operator actions.

## Runtime Boundaries

The migration service must succeed before API/worker startup. API and worker
share the application image and backend network but run different commands.
The parser has its own process, resource limits, read-only filesystem, and
network boundary. Caddy is the ingress boundary. Search uses SearXNG through the
Squid proxy and dedicated networks. Secret files are mounted through Compose
secrets rather than embedded in images/environment defaults.

## Local Invariants

- Preserve service dependency and health conditions; startup order alone is not
  readiness.
- Keep application/parser/search containers read-only and capability-dropped
  where currently configured.
- Do not broaden network attachments or expose backend/parser/database ports
  without a documented trust-boundary reason.
- Keep API token, PostgreSQL password, approval HMAC key, and SearXNG secret out
  of tracked files, image layers, logs, and command output.
- Keep Caddy as the credential-bearing non-loopback HTTPS boundary.
- Remote inference/parser flags and URLs must agree with Python runtime policy.
- Parser limits and upload limits must agree across config, Compose, API, and
  parser service.
- Preserve architecture-specific Ollama settings in their overlays; do not copy
  Jetson assumptions into the base file.
- Credential rotation/reset scripts are destructive operator workflows. Review
  target paths, service impact, and recovery before running them.

## Coordinated Changes

- Service/health/network change: base Compose, every applicable overlay,
  deployment contract, smoke scripts, CI, and runbook note.
- Environment setting: `../src/mongars/config.py`, `../.env.example`, Compose,
  Dockerfile only if build-time, and tests.
- Secret: Compose declaration/mount, config file reader, rotation script,
  examples/documentation without values, and smoke behavior.
- Edge/search change: Caddy/SearXNG/Squid configs and all three local check
  scripts.
- Image/platform change: Dockerfile, lockfile/manifests, supply-chain scanning,
  ARM64/Jetson config, and image smoke tests.

## Safe Editing Rules

Keep one source for each setting and make overrides explicit. Pin CI action/image
inputs according to the existing supply-chain posture. Do not place secret
values in health checks. Avoid shell changes that interpolate unvalidated paths
or print environment files.

## Validation

Working directory: repository root.

```sh
docker compose config --quiet
docker compose -f compose.yaml -f compose.arm64.yaml --profile arm64 config --quiet
docker compose -f compose.yaml -f compose.jetson.yaml --profile jetson config --quiet
uv run python scripts/check_deployment_contract.py
bash deploy/caddy/check.sh
bash deploy/searxng/check.sh
bash deploy/egress-proxy/check.sh
docker build --tag mongars:ci .
bash scripts/deployment_smoke.sh
```

The smoke script starts/changes containers and writes artifacts; inspect its
environment and use an isolated deployment before running it.

## Common Failure Modes

- A base Compose edit invalidates only an optional/profile overlay.
- A service is running but not ready when a dependent process starts.
- Adding a network to solve connectivity bypasses parser/search/ingress
  isolation.
- Supplying secrets as ordinary environment variables or image build args.
- Changing a Python default without changing the Compose-provided value.
- Treating successful config rendering as runtime smoke validation.
- Ignoring the default-render warnings for unset
  `MONGARS_OLLAMA_IMAGE_ARM64`/`MONGARS_OLLAMA_IMAGE_JETSON` and then attempting
  to deploy the corresponding profile without an image.

## Parent and Child Guidance

Parent: [repository AGENTS.md](../AGENTS.md). There is no child `AGENTS.md`;
Caddy, SearXNG, and Squid share this deployment trust boundary.
