from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_ci_local_keeps_docker_stderr_out_of_container_id(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    container_id = "a" * 64
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >> "${{MONGARS_TEST_DOCKER_LOG:?}}"
case "${{1:-}}" in
  info)
    ;;
  pull)
    printf '%s\\n' 'mock pull progress' >&2
    ;;
  run)
    if [[ " $* " == *" --detach "* ]]; then
      printf '%s\\n' 'mock daemon warning' >&2
      printf '%s\\n' '{container_id}'
    fi
    ;;
  inspect)
    printf '%s\\n' 'healthy'
    ;;
  port)
    printf '%s\\n' '127.0.0.1:55432'
    ;;
esac
""",
    )
    for command in ("uv", "shellcheck"):
        _write_executable(fake_bin / command, "#!/usr/bin/env bash\nexit 0\n")

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["MONGARS_TEST_DOCKER_LOG"] = str(docker_log)
    completed = subprocess.run(  # noqa: S603 -- fixed repository script under a mocked PATH
        ["/usr/bin/bash", str(root / "scripts" / "ci-local.sh")],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "mock pull progress" in completed.stderr
    assert "mock daemon warning" in completed.stderr
    docker_calls = docker_log.read_text(encoding="utf-8").splitlines()
    inspect_call = next(call for call in docker_calls if call.startswith("inspect "))
    assert inspect_call.endswith(container_id)
    assert "warning" not in inspect_call
    assert docker_calls.index(next(call for call in docker_calls if call.startswith("pull "))) < (
        docker_calls.index(next(call for call in docker_calls if call.startswith("run --detach ")))
    )
