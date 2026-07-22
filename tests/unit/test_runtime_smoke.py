from __future__ import annotations

import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any
from uuid import uuid4


def _load_runtime_smoke() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "runtime_smoke.py"
    spec = spec_from_file_location("mongars_test_runtime_smoke", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runtime_smoke = _load_runtime_smoke()


def test_partial_artifact_cleanup_builds_safe_targeted_deletes(monkeypatch: Any) -> None:
    task_id = uuid4()
    artifact = runtime_smoke.CreatedArtifact(task_id=task_id, trace_id="partial_trace_123")
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runtime_smoke.subprocess, "run", fake_run)
    runtime_smoke._cleanup_with_compose(artifact, compose_project="mongars-test")

    assert captured["command"][:5] == [
        "docker",
        "compose",
        "--project-name",
        "mongars-test",
        "exec",
    ]
    sql = captured["input"]
    assert "DELETE FROM episodic_events WHERE trace_id = :'smoke_trace_id';" in sql
    assert "DELETE FROM task_queue WHERE id = :'smoke_task_id'::uuid;" in sql
    assert "DELETE FROM memory_documents WHERE metadata ->> 'task_id' = :'smoke_task_id';" in sql
    assert "--set" in captured["command"]
    assert "smoke_trace_id=partial_trace_123" in captured["command"]
    assert f"smoke_task_id={task_id}" in captured["command"]


def test_main_cleans_partially_created_artifacts_after_smoke_failure(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("smoke-token\n", encoding="utf-8")
    task_id = uuid4()
    cleaned: list[runtime_smoke.CreatedArtifact] = []

    def fail_after_task_creation(**kwargs: Any) -> Any:
        artifact = kwargs["artifact"]
        artifact.task_id = task_id
        artifact.trace_id = "partial_trace_456"
        raise runtime_smoke.SmokeFailure("synthetic failure after task creation")

    def record_cleanup(
        artifact: runtime_smoke.CreatedArtifact,
        *,
        compose_project: str | None,
    ) -> None:
        assert compose_project == "mongars-test"
        cleaned.append(artifact)

    monkeypatch.setattr(runtime_smoke, "run_smoke", fail_after_task_creation)
    monkeypatch.setattr(runtime_smoke, "_cleanup_with_compose", record_cleanup)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runtime_smoke.py",
            "--token-file",
            str(token_file),
            "--cleanup-with-compose",
            "--compose-project",
            "mongars-test",
        ],
    )

    assert runtime_smoke.main() == 1
    assert len(cleaned) == 1
    assert cleaned[0].task_id == task_id
    assert cleaned[0].trace_id == "partial_trace_456"
    failure = json.loads(capsys.readouterr().err)
    assert failure == {
        "artifacts_cleaned": True,
        "error": "synthetic failure after task creation",
        "status": "failed",
    }
