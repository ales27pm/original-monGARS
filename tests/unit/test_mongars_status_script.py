from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_mongars_status_script_emits_redacted_json_output(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -Eeuo pipefail

output=""
for ((index = 1; index <= $#; index++)); do
  arg="${!index}"
  case "$arg" in
    --output)
      next=$((index + 1))
      output="${!next}"
      ;;
    http*://*/v1/readyz)
      cat <<'JSON' > "$output"
{"status":"ready","dependencies":{"database":{"healthy":true},"inference":{"backend":"ollama","healthy":true,"chat_model_ready":true,"embedding_model_ready":true,"latency_ms":1.2,"error_code":null},"parser":{"healthy":true,"version":"mongars-parser-v1","error_code":null},"worker":{"healthy":true,"status":"healthy","component_id":"worker:primary","instance_id":"11111111-2222-3333-4444-555555555555","version":"0.1.0","git_sha":"abc","last_seen_at":"2026-07-23T12:00:00+00:00","age_seconds":1.5,"error_code":null},"embedding_space":{"healthy":true,"status":"ready","space_id":"space-1","model_alias":"nomic-embed-text","model_digest":"digest","dimension":768,"worker_space_id":null,"total_chunk_count":10,"compatible_chunk_count":10,"legacy_chunk_count":0,"reindex_required":false,"error_code":null}}}
JSON
      ;;
    http*://*/v1/tasks*)
      cat <<'JSON' > "$output"
[{"id":"00000000-0000-0000-0000-000000000001","status":"waiting_approval"},{"id":"00000000-0000-0000-0000-000000000002","status":"done"},{"id":"00000000-0000-0000-0000-000000000003","status":"waiting_approval"}]
JSON
      ;;
  esac
done

echo -n '200'
""",
    )

    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$1" == "compose" ]]; then
  for arg in "$@"; do
    if [[ "$arg" == "ps" ]]; then
      echo -e "api\trunning\thealthy\tUp\nworker\trunning\thealthy\tUp\npostgres\trunning\thealthy\tUp"
      exit 0
    fi
  done
fi

if [[ "$1" == "system" && "$2" == "df" ]]; then
  cat <<'TXT'
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          3         2         1.2GB     0.8GB
TXT
  exit 0
fi

exit 0
""",
    )

    token_file = tmp_path / "api_token.txt"
    token_file.write_text("super-secret-token", encoding="utf-8")

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["MONGARS_STATUS_API_URL"] = "http://127.0.0.1:8000"
    environment["MONGARS_STATUS_API_TOKEN_FILE"] = str(token_file)
    environment["MONGARS_STATUS_API_TOKEN"] = ""

    completed = subprocess.run(
        [
            "/usr/bin/bash",
            str(root / "scripts" / "mongars-status.sh"),
            "--json",
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "super-secret-token" not in completed.stdout
    payload = json.loads(completed.stdout.strip())
    assert payload["status"] == "ready"
    assert payload["http_status"]["readyz"] == 200
    assert payload["pending_approvals"]["count"] == 2
    assert payload["pending_approvals"]["sampled_tasks"] == 3
    assert payload["dependencies"]["embedding"]["model_alias"] == "nomic-embed-text"
    assert isinstance(payload["compose"]["services"], list)
    assert payload["compose"]["services"][0]["name"] == "api"
