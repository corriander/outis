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
        "inventory": False,
        "inventoryProvider": None,
        "download": False,
        "profiles": False,
        "profileEditor": False,
        "profileProvider": None,
        "launch": False,
        "nativeSettings": False,
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_missing_capability_document_fails_closed():
    policy = _run("cookbookUiPolicy(null)")

    assert policy["browse"] is True
    assert policy["download"] is False
    assert policy["launch"] is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_external_artifact_provider_enables_inventory_only():
    policy = _run("cookbookUiPolicy({mode:'native',capabilities:{catalogue:{browse:true},artifact_store:{provider:'directory',list:true,acquire:true,operation_providers:{list:'directory',acquire:'odysseus-native'}},profile_service:{write:true},runtime_controller:{start:true}}})")

    assert policy["inventory"] is True
    assert policy["inventoryProvider"] == "directory"
    assert policy["download"] is True


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_external_profile_service_enables_the_authoring_island():
    policy = _run("cookbookUiPolicy({mode:'external',capabilities:{profile_service:{provider:'external-profile-service',read:false,write:false,external:true,operation_providers:{read:'external-profile-service',write:'external-profile-service'}}}})")

    assert policy["profileEditor"] is True
    assert policy["profileProvider"] == "external-profile-service"
    # read/write gate the inherited host-side routes and stay off.
    assert policy["profiles"] is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_native_profile_write_does_not_enable_the_external_island():
    policy = _run("cookbookUiPolicy({mode:'native',capabilities:{profile_service:{provider:'odysseus-native',read:true,write:true,external:false,operation_providers:{read:'odysseus-native',write:'odysseus-native'}}}})")

    # The inherited local-file editor is available; the external island is not,
    # and must never be switched on by a native write capability.
    assert policy["profiles"] is True
    assert policy["profileEditor"] is False
    assert policy["profileProvider"] is None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_native_cache_listing_is_not_presented_as_external_inventory():
    policy = _run("cookbookUiPolicy({mode:'native',capabilities:{artifact_store:{provider:'odysseus-native',list:true,acquire:true},profile_service:{write:true},runtime_controller:{start:true}}})")

    assert policy["inventory"] is False
    assert policy["inventoryProvider"] is None
