#!/usr/bin/env python3
"""Exercise the deployed monGARS API, worker, memory, auth, and readiness path.

The script intentionally uses only public HTTP endpoints for the smoke workflow.  It
expects the production-like Compose stack (including Ollama and its embedding model) to
already be running. Run it against a disposable stack because it creates one retained
memory note. ``--cleanup-with-compose`` removes only the identifiers accumulated by this
smoke run, including best-effort cleanup when a later check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

_TERMINAL_TASK_STATES = frozenset({"done", "failed", "cancelled"})
_SAFE_TRACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class SmokeFailure(RuntimeError):
    """Raised when the deployed stack violates a smoke-test contract."""


@dataclass(slots=True)
class CreatedArtifact:
    """Identifiers accumulated as the smoke workflow creates durable state."""

    task_id: UUID | None = None
    trace_id: str | None = None
    document_id: UUID | None = None

    @property
    def has_any(self) -> bool:
        return self.task_id is not None or self.trace_id is not None or self.document_id is not None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SmokeFailure(f"{operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{operation} returned a non-object JSON body")
    return payload


def _expect_status(
    response: httpx.Response,
    expected: int | set[int],
    *,
    operation: str,
) -> dict[str, Any]:
    statuses = {expected} if isinstance(expected, int) else expected
    if response.status_code not in statuses:
        raise SmokeFailure(
            f"{operation} returned HTTP {response.status_code}; expected {sorted(statuses)}"
        )
    return _json_object(response, operation=operation)


def _check_readiness(client: httpx.Client) -> dict[str, Any]:
    response = client.get("/v1/readyz")
    body = _expect_status(response, {200, 503}, operation="readiness probe")
    dependencies = body.get("dependencies")
    _require(isinstance(dependencies, dict), "readiness response has no dependencies object")
    database = dependencies.get("database")
    inference = dependencies.get("inference")
    web_search = dependencies.get("web_search")
    worker = dependencies.get("worker")
    parser = dependencies.get("parser")
    embedding_space = dependencies.get("embedding_space")
    _require(isinstance(database, dict), "readiness response has no database object")
    _require(isinstance(inference, dict), "readiness response has no inference object")
    _require(isinstance(web_search, dict), "readiness response has no web-search object")
    _require(isinstance(worker, dict), "readiness response has no worker object")
    _require(isinstance(parser, dict), "readiness response has no parser object")
    _require(
        isinstance(embedding_space, dict),
        "readiness response has no embedding-space object",
    )
    _require(isinstance(database.get("healthy"), bool), "database health is not boolean")
    _require(isinstance(inference.get("healthy"), bool), "inference health is not boolean")
    _require(isinstance(web_search.get("healthy"), bool), "web-search health is not boolean")
    _require(isinstance(web_search.get("enabled"), bool), "web-search enabled is not boolean")
    _require(isinstance(worker.get("healthy"), bool), "worker health is not boolean")
    _require(isinstance(parser.get("healthy"), bool), "parser health is not boolean")
    _require(
        isinstance(embedding_space.get("healthy"), bool),
        "embedding-space health is not boolean",
    )
    _require(inference.get("backend") == "ollama", "readiness did not identify Ollama")

    all_healthy = bool(
        database["healthy"]
        and inference["healthy"]
        and (not web_search["enabled"] or web_search["healthy"])
        and worker["healthy"]
        and parser["healthy"]
        and embedding_space["healthy"]
    )
    expected_status = "ready" if all_healthy else "not_ready"
    expected_http = 200 if all_healthy else 503
    _require(body.get("status") == expected_status, "readiness status contradicts dependencies")
    _require(
        response.status_code == expected_http, "readiness HTTP status contradicts dependencies"
    )
    _require(database["healthy"] is True, "PostgreSQL is not ready")
    _require(worker["healthy"] is True, "worker heartbeat is not ready")
    _require(parser["healthy"] is True, "document parser is not ready")
    _require(embedding_space["healthy"] is True, "embedding space is not ready")
    _require(
        embedding_space.get("reindex_required") is False,
        "owner corpus requires an approved reindex",
    )
    return body


def _poll_task(
    client: httpx.Client,
    task_id: UUID,
    *,
    deadline: float,
    poll_seconds: float,
) -> dict[str, Any]:
    while time.monotonic() < deadline:
        response = client.get(f"/v1/tasks/{task_id}")
        task = _expect_status(response, 200, operation="task status")
        status = task.get("status")
        if status in _TERMINAL_TASK_STATES:
            return task
        time.sleep(poll_seconds)
    raise SmokeFailure(f"task {task_id} did not reach a terminal state before timeout")


def run_smoke(
    *,
    api_url: str,
    token: str,
    timeout_seconds: float,
    poll_seconds: float,
    artifact: CreatedArtifact | None = None,
) -> tuple[dict[str, Any], CreatedArtifact]:
    created_artifact = artifact if artifact is not None else CreatedArtifact()
    parsed_url = urlsplit(api_url)
    _require(
        parsed_url.scheme in {"http", "https"} and parsed_url.hostname is not None,
        "API URL must be an absolute HTTP(S) URL",
    )
    _require(bool(token), "API bearer token must not be empty")

    request_timeout = httpx.Timeout(min(timeout_seconds, 120.0), connect=5.0)
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=request_timeout) as anonymous:
        health = _expect_status(anonymous.get("/v1/healthz"), 200, operation="liveness probe")
        _require(health == {"status": "ok"}, "unexpected liveness response")
        _expect_status(
            anonymous.get("/v1/readyz"),
            401,
            operation="anonymous readiness probe",
        )
        _expect_status(anonymous.get("/v1/tasks"), 401, operation="anonymous protected route")
        with httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={"Authorization": "Bearer deliberately-invalid-smoke-token"},
            timeout=request_timeout,
        ) as invalid:
            _expect_status(
                invalid.get("/v1/readyz"),
                401,
                operation="invalid bearer readiness probe",
            )
            _expect_status(invalid.get("/v1/tasks"), 401, operation="invalid bearer token")

    marker = f"mongars-runtime-smoke-{uuid4().hex}"
    note_text = f"Runtime smoke memory marker: {marker}"
    title = f"Runtime smoke {marker[-12:]}"
    deadline = time.monotonic() + timeout_seconds
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(
        base_url=api_url.rstrip("/"), headers=headers, timeout=request_timeout
    ) as client:
        readiness_before = _check_readiness(client)
        inference = readiness_before["dependencies"]["inference"]
        _require(inference["healthy"] is True, "Ollama is honestly reported unavailable")

        created = _expect_status(
            client.post(
                "/v1/memory/documents",
                json={
                    "text": note_text,
                    "title": title,
                    "sensitivity": "private",
                    "retention_class": "ttl_30d",
                },
            ),
            202,
            operation="memory task creation",
        )
        task_id = UUID(str(created.get("id")))
        created_artifact.task_id = task_id
        raw_trace_id = created.get("trace_id")
        _require(
            isinstance(raw_trace_id, str) and _SAFE_TRACE_ID.fullmatch(raw_trace_id) is not None,
            "memory task creation returned an invalid trace ID",
        )
        trace_id = raw_trace_id
        created_artifact.trace_id = trace_id
        _require(created.get("kind") == "memory.note.create", "unexpected memory task kind")
        _require(created.get("risk_level") == "local_mutation", "memory write was misclassified")
        _require(created.get("status") == "waiting_approval", "memory write bypassed approval")

        review = _expect_status(
            client.get(f"/v1/tasks/{task_id}"),
            200,
            operation="memory task review",
        )
        action_digest = review.get("action_digest")
        _require(
            isinstance(action_digest, str) and len(action_digest) == 64,
            "memory task review returned an invalid action digest",
        )

        approved = _expect_status(
            client.post(
                f"/v1/tasks/{task_id}/approve",
                json={"action_digest": action_digest},
            ),
            200,
            operation="memory task approval",
        )
        _require(approved.get("status") == "queued", "approved task was not queued")
        _require(approved.get("approved_at") is not None, "approved task has no approval timestamp")

        completed = _poll_task(
            client,
            task_id,
            deadline=deadline,
            poll_seconds=poll_seconds,
        )
        _require(
            completed.get("status") == "done", f"worker task failed: {completed.get('error_text')}"
        )
        _require(int(completed.get("attempt_count", 0)) >= 1, "worker did not claim the task")
        result = completed.get("result")
        _require(isinstance(result, dict), "completed task has no result object")
        document_id = UUID(str(result.get("document_id")))
        created_artifact.document_id = document_id
        _require(result.get("created") is True, "unique smoke document was not created")
        _require(int(result.get("chunk_count", 0)) >= 1, "smoke document has no chunks")

        document = _expect_status(
            client.get(f"/v1/memory/documents/{document_id}"),
            200,
            operation="memory document retrieval",
        )
        _require(document.get("id") == str(document_id), "retrieved the wrong document")
        _require(document.get("title") == title, "retrieved document title does not match")
        metadata = document.get("metadata")
        _require(isinstance(metadata, dict), "retrieved document metadata is invalid")
        _require(metadata.get("task_id") == str(task_id), "document provenance has the wrong task")

        _expect_status(
            client.get(f"/v1/memory/documents/{document_id}", headers={"Authorization": ""}),
            401,
            operation="anonymous document retrieval",
        )
        search = _expect_status(
            client.post(
                "/v1/memory/search",
                json={"query": marker, "top_k": 10, "mode": "hybrid"},
            ),
            200,
            operation="memory search",
        )
        hits = search.get("hits")
        _require(isinstance(hits, list), "memory search returned no hits list")
        matching_hits = [
            hit
            for hit in hits
            if isinstance(hit, dict)
            and hit.get("document_id") == str(document_id)
            and marker in str(hit.get("text", ""))
        ]
        _require(bool(matching_hits), "inserted memory was not returned by retrieval")
        readiness_after = _check_readiness(client)

    summary = {
        "status": "passed",
        "api_url": api_url.rstrip("/"),
        "health": "ok",
        "database_healthy": readiness_after["dependencies"]["database"]["healthy"],
        "ollama_healthy": readiness_after["dependencies"]["inference"]["healthy"],
        "task_id": str(task_id),
        "task_status": completed["status"],
        "document_id": str(document_id),
        "retrieval_hits": len(matching_hits),
    }
    return summary, created_artifact


def _cleanup_with_compose(artifact: CreatedArtifact, *, compose_project: str | None) -> None:
    if not artifact.has_any:
        return
    if artifact.trace_id is not None:
        _require(_SAFE_TRACE_ID.fullmatch(artifact.trace_id) is not None, "unsafe trace ID")
    postgres_user = os.getenv("POSTGRES_USER", "mongars")
    postgres_database = os.getenv("POSTGRES_DB", "mongars")
    _require(re.fullmatch(r"[A-Za-z0-9_-]{1,63}", postgres_user) is not None, "unsafe DB user")
    _require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,63}", postgres_database) is not None,
        "unsafe DB name",
    )
    command = ["docker", "compose"]
    if compose_project:
        command.extend(["--project-name", compose_project])
    command.extend(
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "--set=ON_ERROR_STOP=1",
            "--username",
            postgres_user,
            "--dbname",
            postgres_database,
        ]
    )
    if artifact.trace_id is not None:
        command.extend(["--set", f"smoke_trace_id={artifact.trace_id}"])
    if artifact.document_id is not None:
        command.extend(["--set", f"smoke_document_id={artifact.document_id}"])
    if artifact.task_id is not None:
        command.extend(["--set", f"smoke_task_id={artifact.task_id}"])
    statements = ["BEGIN;"]
    if artifact.trace_id is not None:
        statements.append("DELETE FROM episodic_events WHERE trace_id = :'smoke_trace_id';")
    if artifact.document_id is not None and artifact.task_id is not None:
        statements.append(
            "DELETE FROM memory_documents "
            "WHERE id = :'smoke_document_id'::uuid "
            "OR metadata ->> 'task_id' = :'smoke_task_id';"
        )
    elif artifact.document_id is not None:
        statements.append("DELETE FROM memory_documents WHERE id = :'smoke_document_id'::uuid;")
    elif artifact.task_id is not None:
        statements.append(
            "DELETE FROM memory_documents WHERE metadata ->> 'task_id' = :'smoke_task_id';"
        )
    if artifact.task_id is not None:
        statements.append("DELETE FROM task_queue WHERE id = :'smoke_task_id'::uuid;")
    statements.append("COMMIT;")
    sql = "\n".join(statements) + "\n"
    completed = subprocess.run(  # noqa: S603 -- fixed executable and validated arguments
        command,
        input=sql,
        text=True,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise SmokeFailure("exact-artifact Compose cleanup failed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.getenv("MONGARS_SMOKE_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(os.getenv("MONGARS_SMOKE_API_TOKEN_FILE", "secrets/api_token.txt")),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("MONGARS_SMOKE_TIMEOUT_SECONDS", "240")),
    )
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument(
        "--cleanup-with-compose",
        action="store_true",
        help="delete only this run's document, task, and events via the local Compose database",
    )
    parser.add_argument(
        "--compose-project",
        default=os.getenv("COMPOSE_PROJECT_NAME"),
        help="Compose project name used only with --cleanup-with-compose",
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    artifact = CreatedArtifact()
    summary: dict[str, Any] | None = None
    failure_message: str | None = None
    artifacts_cleaned = False
    try:
        _require(arguments.timeout_seconds > 0, "timeout must be positive")
        _require(arguments.poll_seconds > 0, "poll interval must be positive")
        token = arguments.token_file.read_text(encoding="utf-8").strip()
        summary, artifact = run_smoke(
            api_url=arguments.api_url,
            token=token,
            timeout_seconds=arguments.timeout_seconds,
            poll_seconds=arguments.poll_seconds,
            artifact=artifact,
        )
    except (OSError, ValueError, httpx.HTTPError, SmokeFailure) as exc:
        failure_message = str(exc)
    finally:
        if arguments.cleanup_with_compose and artifact.has_any:
            try:
                _cleanup_with_compose(artifact, compose_project=arguments.compose_project)
                artifacts_cleaned = True
            except (OSError, SmokeFailure) as exc:
                cleanup_message = str(exc)
                failure_message = (
                    cleanup_message
                    if failure_message is None
                    else f"{failure_message}; cleanup also failed: {cleanup_message}"
                )

    if failure_message is not None:
        failure = {"status": "failed", "error": failure_message}
        if artifacts_cleaned:
            failure["artifacts_cleaned"] = True
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1
    if summary is None:
        print(
            json.dumps({"status": "failed", "error": "smoke run produced no summary"}),
            file=sys.stderr,
        )
        return 1
    if artifacts_cleaned:
        summary["artifacts_cleaned"] = True
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
