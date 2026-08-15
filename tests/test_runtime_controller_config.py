"""RuntimeController configuration stays independent, opaque, and encrypted."""

import json

import pytest
from runtime_controller.config import (
    RuntimeControllerConfiguration,
    RuntimeControllerConfigurationError,
)


@pytest.fixture
def configured_paths(tmp_path, monkeypatch):
    import runtime_controller.config as config
    import src.managed_transaction as transaction
    import src.secret_storage as secret_storage

    active = tmp_path / "runtime_controller.json"
    candidate = tmp_path / "runtime_controller.pending.json"
    transaction_path = tmp_path / "managed_bootstrap.json"
    monkeypatch.setattr(config, "RUNTIME_CONTROLLER_CONFIG_FILE", str(active))
    monkeypatch.setattr(config, "RUNTIME_CONTROLLER_CANDIDATE_FILE", str(candidate))
    monkeypatch.setattr(transaction, "MANAGED_BOOTSTRAP_FILE", str(transaction_path))
    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    for name in (
        "OUTIS_RUNTIME_CONTROLLER_URL",
        "OUTIS_RUNTIME_CONTROLLER_NAME",
        "OUTIS_RUNTIME_CONTROLLER_TOKEN",
        "OUTIS_RUNTIME_CONTROLLER_TARGET",
        "OUTIS_RUNTIME_CONTROLLER_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    transaction.write_transaction(revision="revision-1", managed_admin_username="admin")
    return config, active, candidate


def test_environment_configuration_requires_explicit_target(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    monkeypatch.setenv("OUTIS_RUNTIME_CONTROLLER_URL", "http://controller.test:8850")

    with pytest.raises(RuntimeControllerConfigurationError, match="target is required"):
        config.environment_configuration()


def test_target_is_one_opaque_path_segment(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    monkeypatch.setenv("OUTIS_RUNTIME_CONTROLLER_URL", "http://controller.test:8850")
    monkeypatch.setenv("OUTIS_RUNTIME_CONTROLLER_TARGET", "host/deployment")

    with pytest.raises(RuntimeControllerConfigurationError, match="opaque path segment"):
        config.environment_configuration()


def test_persisted_configuration_encrypts_token_and_retains_target(configured_paths):
    config, active, candidate = configured_paths
    supplied = RuntimeControllerConfiguration(
        base_url="http://controller.test:8850/",
        token="controller-token",
        target="deployment-noun",
        name="Managed runtime",
        timeout_seconds=75,
    )

    config.write_candidate(supplied)
    loaded_candidate = config.load_candidate()
    config.activate_candidate(
        loaded_candidate,
        revision="revision-1",
        managed_admin_username="admin",
        verified=True,
    )
    loaded = config.load_persisted_configuration()

    assert loaded is not None
    assert loaded.target == "deployment-noun"
    assert loaded.token == "controller-token"
    assert loaded.source == "persisted"
    assert loaded.verified is True
    text = active.read_text(encoding="utf-8")
    assert "controller-token" not in text
    assert json.loads(text)["configuration"]["token"].startswith("enc:")
    assert not candidate.exists()


def test_persisted_configuration_is_authoritative_as_a_whole(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    supplied = RuntimeControllerConfiguration(
        base_url="http://controller.test:8850",
        token="persisted-token",
        target="persisted-target",
    )
    config.write_candidate(supplied)
    config.activate_candidate(
        config.load_candidate(),
        revision="revision-1",
        managed_admin_username="admin",
        verified=False,
    )
    monkeypatch.setenv("OUTIS_RUNTIME_CONTROLLER_URL", "http://wrong.test")
    monkeypatch.setenv("OUTIS_RUNTIME_CONTROLLER_TARGET", "wrong-target")

    loaded = config.resolve_runtime_controller_configuration()

    assert loaded is not None
    assert loaded.base_url == "http://controller.test:8850"
    assert loaded.target == "persisted-target"
