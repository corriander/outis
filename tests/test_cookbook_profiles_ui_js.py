"""Small pure checks for the profile application UI adapter."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "cookbookProfiles.js"
HAS_NODE = shutil.which("node") is not None


def _run(expression: str):
    script = (
        f"import {{ profilesPanelHtml, runtimeApplySummary, runtimeEvictionConflict }} "
        f"from '{MODULE.as_uri()}';"
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
def test_apply_button_is_only_offered_for_a_configured_controller():
    enabled = _run("profilesPanelHtml({available:true, applyAvailable:true, runtimeProvider:'Controller'})")
    disabled = _run("profilesPanelHtml({available:true, applyAvailable:false})")

    assert 'id="cookbook-profile-apply" disabled>Apply profiles</button>' in enabled
    assert 'Runtime: Controller' in enabled
    assert 'id="cookbook-profile-apply" hidden disabled' in disabled


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_only_would_evict_opens_the_continue_wait_path():
    body = {
        "errors": [
            {
                "pointer": "/",
                "code": "would_evict",
                "message": "would evict",
                "meta": {"models": ["loaded-model"]},
            }
        ],
        "warnings": [],
    }
    encoded = json.dumps(body)

    assert _run(f"runtimeEvictionConflict(409, {encoded})?.meta.models") == [
        "loaded-model"
    ]
    assert _run(f"runtimeEvictionConflict(502, {encoded})") is None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_success_summary_reports_provider_truth():
    body = {
        "data": {
            "configuration": "changed",
            "state": "available",
            "evicted": ["old-model"],
        },
        "warnings": [{"code": "restart_warning", "message": "Recovered slowly."}],
    }

    summary = _run(f"runtimeApplySummary({json.dumps(body)})")

    assert summary == (
        "Saved profiles applied. Configuration changed. Runtime available. "
        "Evicted: old-model. Recovered slowly."
    )
