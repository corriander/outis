"""Capability-boundary tests for the external ProfileService adapter.

The external ProfileService is an additive, independently-selectable provider.
It must never re-enable the inherited host-side profile routes, and the
synchronous capability document must not invent the accepted artifact
authorities (those are advertised by the service's own discovery document).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


def _profile_capability(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    from src.cookbook_capabilities import cookbook_capabilities

    return cookbook_capabilities()["capabilities"]["profile_service"]


def test_native_mode_profile_service_is_unchanged(monkeypatch):
    profile = _profile_capability(
        monkeypatch,
        OUTIS_COOKBOOK_MODE="native",
        OUTIS_PROFILE_SERVICE_URL=None,
    )
    assert profile["provider"] == "odysseus-native"
    assert profile["read"] is True
    assert profile["write"] is True
    assert profile["external"] is False


def test_external_mode_without_provider_is_catalogue_only(monkeypatch):
    profile = _profile_capability(
        monkeypatch,
        OUTIS_COOKBOOK_MODE="external",
        OUTIS_PROFILE_SERVICE_URL=None,
    )
    assert profile["read"] is False
    assert profile["write"] is False
    assert profile["external"] is False


@pytest.mark.parametrize("mode", ["native", "external"])
def test_configured_external_profile_service_advertises_identity(monkeypatch, mode):
    profile = _profile_capability(
        monkeypatch,
        OUTIS_COOKBOOK_MODE=mode,
        OUTIS_PROFILE_SERVICE_URL="http://profile.invalid:8850",
        OUTIS_PROFILE_SERVICE_NAME="Studio profiles",
    )
    assert profile["external"] is True
    assert profile["provider"] == "Studio profiles"
    assert profile["operation_providers"]["read"] == "Studio profiles"
    assert profile["operation_providers"]["write"] == "Studio profiles"


def test_external_provider_does_not_re_enable_inherited_routes(monkeypatch):
    """The proxy owns external read/write; the inherited host-side routes stay
    gated on the native flags even when an external service is configured."""
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", "http://profile.invalid:8850")

    from src.cookbook_capabilities import require_cookbook_capability

    for action in ("read", "write"):
        with pytest.raises(HTTPException) as exc:
            require_cookbook_capability("profile_service", action)
        assert exc.value.status_code == 501
        assert f"profile_service.{action}" in exc.value.detail


def test_inventory_and_profile_providers_are_independently_selectable(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", "http://profile.invalid:8850")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_NAME", "profiles")
    monkeypatch.delenv("OUTIS_ARTIFACT_STORE_URL", raising=False)

    from src.cookbook_capabilities import cookbook_capabilities

    capabilities = cookbook_capabilities()["capabilities"]
    # A configured profile service leaves inventory untouched...
    assert capabilities["artifact_store"]["list"] is False
    assert capabilities["profile_service"]["external"] is True

    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_URL", "http://inventory.invalid:7331")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_NAME", "directory")
    monkeypatch.delenv("OUTIS_PROFILE_SERVICE_URL", raising=False)

    capabilities = cookbook_capabilities()["capabilities"]
    # ...and a configured inventory does not switch the profile adapter on.
    assert capabilities["artifact_store"]["list"] is True
    assert capabilities["profile_service"]["external"] is False


def test_accepted_authorities_are_not_invented_in_sync_document(monkeypatch):
    profile = _profile_capability(
        monkeypatch,
        OUTIS_COOKBOOK_MODE="external",
        OUTIS_PROFILE_SERVICE_URL="http://profile.invalid:8850",
    )
    # Accepted authorities come from live discovery, not from the synchronous
    # capability document, which cannot honestly enumerate them without I/O.
    assert "accepted_authorities" not in profile
