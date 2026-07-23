"""Behavioral checks for the frontend's capability-to-UI policy."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "cookbookCapabilities.js"
HAS_NODE = shutil.which("node") is not None


def _run(expression: str):
    script = (
        f"import {{ cookbookUiPolicy }} from '{MODULE.as_uri()}';"
        f"console.log(JSON.stringify({expression}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_external_mode_keeps_browse_and_removes_native_actions():
    policy = _run("cookbookUiPolicy({capabilities:{catalogue:{browse:true,inspect:true},artifact_store:{acquire:false},profile_service:{write:false},runtime_controller:{start:false}}})")

    assert policy == {
        "browse": True,
        "download": False,
        "profiles": False,
        "launch": False,
        "nativeSettings": False,
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_missing_capability_document_fails_closed():
    policy = _run("cookbookUiPolicy(null)")

    assert policy["browse"] is True
    assert policy["download"] is False
    assert policy["launch"] is False
