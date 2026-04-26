"""End-to-end smoke tests for the built CUE4ParseCLI.exe.

Skipped automatically when the exe is absent. Set CUE4PARSE_CLI to override
the default in-tree publish location.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.requires_dotnet_cli, pytest.mark.slow]


def _resolve_cli() -> str:
    cli = os.environ.get("CUE4PARSE_CLI")
    if cli and Path(cli).is_file():
        return cli
    repo_root = Path(__file__).resolve().parent.parent.parent
    default = repo_root / "cue4parse_cli" / "bin" / "publish" / "CUE4ParseCLI.exe"
    return str(default)


def test_cli_starts_and_quits_cleanly():
    """Start the CLI, send `quit`, expect exit 0 within 5 seconds."""
    cli = _resolve_cli()
    proc = subprocess.Popen(
        [cli],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,  # NDJSON is bytes
    )
    try:
        proc.stdin.write(json.dumps({"cmd": "quit"}).encode("utf-8") + b"\n")
        proc.stdin.flush()
        rc = proc.wait(timeout=10)
        assert rc == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
