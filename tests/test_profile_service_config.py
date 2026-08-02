"""Contract tests for ProfileService configuration resolution.

All providers, URLs, and credentials here are synthetic. The point under test
is the resolution contract rather than transport: managed persistence and the
environment are two complete alternative sources, never merged, and the
ProfileService role is never satisfied by ArtifactStore state.
"""

from __future__ import annotations

import json

import pytest

from core.atomic_io import atomic_write_json


ENV_VARS = (
    "OUTIS_PROFILE_SERVICE_URL",
    "OUTIS_PROFILE_SERVICE_TOKEN",
    "OUTIS_PROFILE_SERVICE_NAME",
    "OUTIS_PROFILE_SERVICE_TIMEOUT",
)


@pytest.fixture
def config_state(tmp_path, monkeypatch):
    import profile_service.config as config
    import src.secret_storage as secret_storage

    active_path = tmp_path / "profile_service.json"
    candidate_path = tmp_path / "profile_service.pending.json"

    monkeypatch.setattr(config, "PROFILE_SERVICE_CONFIG_FILE", str(active_path))
    monkeypatch.setattr(config, "PROFILE_SERVICE_CANDIDATE_FILE", str(candidate_path))
    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    return {
        "active": active_path,
        "candidate": candidate_path,
        "config": config,
        "secret_storage": secret_storage,
    }


def _set_env(monkeypatch, *, url="https://env.invalid/base/", token="env-token"):
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", url)
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TOKEN", token)
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_NAME", "Environment profiles")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TIMEOUT", "12")


def _persist(config_state, **overrides):
    """Write an active managed document the way a bootstrap run would."""
    config = config_state["config"]
    configuration = config.ProfileServiceConfiguration(
        base_url=overrides.pop("base_url", "https://managed.invalid"),
        token=overrides.pop("token", "managed-token"),
        name=overrides.pop("name", "Managed profiles"),
        timeout_seconds=overrides.pop("timeout_seconds", 30.0),
    )
    return config.activate_candidate(
        configuration,
        revision=overrides.pop("revision", "rev-1"),
        managed_admin_username=overrides.pop("managed_admin_username", "ManagedAdmin"),
        verified=overrides.pop("verified", False),
    )


# -- source selection ------------------------------------------------------


def test_unconfigured_resolves_to_nothing(config_state):
    config = config_state["config"]
    assert config.resolve_profile_service_configuration() is None
    assert config.persisted_configuration_present() is False


def test_environment_is_used_when_no_managed_state(config_state, monkeypatch):
    config = config_state["config"]
    _set_env(monkeypatch)

    configuration = config.resolve_profile_service_configuration()

    assert configuration is not None
    assert configuration.source == "environment"
    assert configuration.base_url == "https://env.invalid/base"
    assert configuration.token == "env-token"
    assert configuration.name == "Environment profiles"
    assert configuration.timeout_seconds == 12.0
    assert configuration.revision is None


def test_persisted_configuration_wins_over_environment(config_state, monkeypatch):
    config = config_state["config"]
    _set_env(monkeypatch)
    _persist(config_state)

    configuration = config.resolve_profile_service_configuration()

    assert configuration is not None
    assert configuration.source == "persisted"
    assert configuration.revision == "rev-1"
    # Every field comes from the managed document; nothing is taken from the
    # environment, which sets a different URL, token, name, and timeout.
    assert configuration.base_url == "https://managed.invalid"
    assert configuration.token == "managed-token"
    assert configuration.name == "Managed profiles"
    assert configuration.timeout_seconds == 30.0
    assert configuration.managed_admin_username == "managedadmin"


def test_removing_managed_state_falls_back_to_the_environment(config_state, monkeypatch):
    config = config_state["config"]
    _set_env(monkeypatch)
    _persist(config_state)

    config_state["active"].unlink()

    configuration = config.resolve_profile_service_configuration()
    assert configuration is not None
    assert configuration.source == "environment"
    assert configuration.base_url == "https://env.invalid/base"


# -- managed document integrity -------------------------------------------


def test_persisted_document_without_a_revision_is_rejected(config_state):
    config = config_state["config"]
    _persist(config_state)

    document = json.loads(config_state["active"].read_text(encoding="utf-8"))
    document.pop("revision")
    atomic_write_json(str(config_state["active"]), document, indent=2)

    with pytest.raises(config.ProfileServiceConfigurationError):
        config.resolve_profile_service_configuration()


def test_persisted_document_with_an_unsupported_schema_is_rejected(config_state):
    config = config_state["config"]
    _persist(config_state)

    document = json.loads(config_state["active"].read_text(encoding="utf-8"))
    document["schema_version"] = config.CONFIG_SCHEMA_VERSION + 1
    atomic_write_json(str(config_state["active"]), document, indent=2)

    with pytest.raises(config.ProfileServiceConfigurationError):
        config.resolve_profile_service_configuration()


def test_an_artifact_store_credential_is_not_a_profile_service_credential(config_state):
    """The roles are independent: neither is inferred from the other."""
    config = config_state["config"]
    secret_storage = config_state["secret_storage"]
    _persist(config_state)

    document = json.loads(config_state["active"].read_text(encoding="utf-8"))
    document["configuration"]["token"] = secret_storage.encrypt(
        "artifact-store-token:managed-token"
    )
    atomic_write_json(str(config_state["active"]), document, indent=2)

    with pytest.raises(config.ProfileServiceConfigurationError, match="credential"):
        config.resolve_profile_service_configuration()


def test_a_bearer_beginning_with_enc_survives_the_round_trip(config_state):
    config = config_state["config"]
    _persist(config_state, token="enc:not-actually-encrypted")

    configuration = config.resolve_profile_service_configuration()
    assert configuration is not None
    assert configuration.token == "enc:not-actually-encrypted"


# -- candidate lifecycle ---------------------------------------------------


def test_activating_a_candidate_discards_it(config_state):
    config = config_state["config"]
    config.write_candidate(
        config.ProfileServiceConfiguration(
            base_url="https://managed.invalid", token="managed-token"
        )
    )
    assert config_state["candidate"].exists()

    candidate = config.load_candidate()
    assert candidate.source == "candidate"

    config.activate_candidate(
        candidate, revision="rev-1", managed_admin_username="admin", verified=False
    )

    assert not config_state["candidate"].exists()
    assert config_state["active"].exists()


def test_a_candidate_without_a_credential_is_refused(config_state):
    config = config_state["config"]
    with pytest.raises(config.ProfileServiceConfigurationError):
        config.write_candidate(
            config.ProfileServiceConfiguration(
                base_url="https://managed.invalid", token=None
            )
        )


def test_same_provider_configuration_compares_the_whole_provider(config_state):
    config = config_state["config"]
    left = config.ProfileServiceConfiguration(
        base_url="https://managed.invalid", token="managed-token", name="Managed"
    )

    assert config.same_provider_configuration(None, left) is False
    assert config.same_provider_configuration(left, left) is True

    from dataclasses import replace

    assert config.same_provider_configuration(replace(left, token="other"), left) is False
    assert config.same_provider_configuration(replace(left, name="Other"), left) is False
    assert (
        config.same_provider_configuration(replace(left, timeout_seconds=5.0), left)
        is False
    )


# -- what the capability surface and client see ----------------------------


def test_capability_helpers_follow_managed_state(config_state, monkeypatch):
    from profile_service.client import (
        configured_profile_service_name,
        profile_service_configured,
    )

    assert profile_service_configured() is False
    assert configured_profile_service_name() == config_state["config"].DEFAULT_PROVIDER_NAME

    _persist(config_state)

    assert profile_service_configured() is True
    assert configured_profile_service_name() == "Managed profiles"


def test_an_unreadable_managed_document_still_advertises_the_provider(config_state):
    """A broken managed file is intent, not absence.

    The capability stays advertised so the proxy reports a configuration
    fault rather than pretending the service was never configured.
    """
    from profile_service.client import (
        configured_profile_service_name,
        profile_service_configured,
    )

    config_state["active"].write_text("{not json", encoding="utf-8")

    assert profile_service_configured() is True
    assert configured_profile_service_name() == config_state["config"].DEFAULT_PROVIDER_NAME


def test_client_is_built_from_managed_state(config_state, monkeypatch):
    from profile_service.client import ProfileServiceClient

    _set_env(monkeypatch)
    _persist(config_state)

    client = ProfileServiceClient.from_config()

    assert client is not None
    assert client.base_url == "https://managed.invalid"
    assert client.token == "managed-token"
    assert client.name == "Managed profiles"
    assert client.timeout_seconds == 30.0


def test_from_env_is_an_alias_for_the_unified_resolver(config_state, monkeypatch):
    from profile_service.client import ProfileServiceClient

    _persist(config_state)

    from_env = ProfileServiceClient.from_env()
    from_config = ProfileServiceClient.from_config()

    assert from_env is not None and from_config is not None
    assert from_env.base_url == from_config.base_url
    assert from_env.token == from_config.token
    assert from_env.name == from_config.name


def test_a_broken_managed_document_surfaces_as_a_client_error(config_state):
    from profile_service.client import ProfileServiceClient, ProfileServiceError

    config_state["active"].write_text("{not json", encoding="utf-8")

    with pytest.raises(ProfileServiceError):
        ProfileServiceClient.from_config()
