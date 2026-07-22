#!/usr/bin/env python3
"""Deterministic HTTP fixtures for the deployment-level Compose smoke."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_EMBEDDING_DIMENSIONS = 768


class Handler(BaseHTTPRequestHandler):
    server_version = "monGARS-ci-mock"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._json({"status": "ok"})
        elif path == "/api/tags":
            self._json(
                {
                    "models": [
                        {"name": "qwen3:4b-instruct"},
                        {"name": "nomic-embed-text"},
                    ]
                }
            )
        elif path == "/config":
            self._json({"formats": ["json"], "instance_name": "CI search fixture"})
        elif path == "/search":
            self._json(
                {
                    "results": [
                        {
                            "title": "Deterministic deployment result",
                            "url": "https://example.com/mongars-ci-result",
                            "content": "The deterministic deployment smoke result is verified.",
                            "engine": "ci-fixture",
                        }
                    ]
                }
            )
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        payload = self._request_json()
        if path == "/api/chat":
            self._json(
                {
                    "model": payload.get("model", "qwen3:4b-instruct"),
                    "message": {
                        "role": "assistant",
                        "content": "The deterministic deployment smoke result is verified.",
                    },
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 12,
                    "eval_count": 8,
                }
            )
        elif path == "/api/embed":
            inputs = payload.get("input", [])
            vector = [1.0, *([0.0] * (_EMBEDDING_DIMENSIONS - 1))]
            self._json(
                {
                    "model": payload.get("model", "nomic-embed-text"),
                    "embeddings": [vector for _ in inputs],
                }
            )
        else:
            self._json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _request_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _json(self, payload: object, *, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.getenv("MONGARS_CI_MOCK_PORT", "8090"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104
