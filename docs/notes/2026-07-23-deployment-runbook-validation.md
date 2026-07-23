# Deployment runbook validation (2026-07-23)

Executed the end-to-end monGARS deployment runbook on `main` at commit `ffad7a2c41e16062f7237e60c966582de14cac58`.

## Completed validations

- Synchronized repo to `ffad7a2c...` and preserved existing `.env` + `secrets/` files.
- Built and started full stack with `--profile gpu --profile web-search`.
- PostgreSQL backups taken from existing deployment prior to migration.
- Performed schema migration via dedicated migrate service.
- Configured and verified HTTPS + LAN bindings with Caddy local CA.
- Re-ran readiness loop and corrected `embedding_reindex_required` via protected reindex flow.
- Ran `make ci-local` and completed all checks.
- Ran runtime smoke test: `uv run python scripts/runtime_smoke.py --cleanup-with-compose`.
- Performed real Ollama connectivity smoke (`tests/inference`) successfully after ensuring host-visible Ollama endpoint.
- Ran mobile package validation:
  - `npm ci`
  - `npm run lint`
  - `npm run typecheck`
  - `npm test`

## Notes

- Runtime checks at the end reported `/v1/healthz` as `ok` and `/v1/readyz` as `ready` with Ollama, parser, worker, web search, and embedding space healthy.
- Temporary host-networked Ollama container was used only for inference command execution and was removed after tests.
