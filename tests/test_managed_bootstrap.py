import json

import httpx
import pytest


@pytest.fixture
def bootstrap_state(tmp_path, monkeypatch):
    import artifact_store.config as config
    import src.managed_bootstrap as bootstrap
    import src.secret_storage as secret_storage

    auth_path = tmp_path / "auth.json"
    active_path = tmp_path / "artifact_store.json"
    candidate_path = tmp_path / "artifact_store.pending.json"
    key_path = tmp_path / ".app_key"

    monkeypatch.setattr(config, "ARTIFACT_STORE_CONFIG_FILE", str(active_path))
    monkeypatch.setattr(config, "ARTIFACT_STORE_CANDIDATE_FILE", str(candidate_path))
    monkeypatch.setattr(bootstrap, "AUTH_FILE", str(auth_path))
    monkeypatch.setattr(secret_storage, "_KEY_PATH", key_path)
    monkeypatch.setattr(secret_storage, "_fernet", None)
    for name in (
        "ODYSSEUS_ADMIN_USER",
        "ODYSSEUS_ADMIN_PASSWORD",
        "OUTIS_ARTIFACT_STORE_URL",
        "OUTIS_ARTIFACT_STORE_NAME",
        "OUTIS_ARTIFACT_STORE_TOKEN",
        "OUTIS_ARTIFACT_STORE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)

    return {
        "auth": auth_path,
        "active": active_path,
        "candidate": candidate_path,
        "key": key_path,
        "config": config,
        "bootstrap": bootstrap,
        "auth_manager_cls": bootstrap.AuthManager,
    }


def _set_inputs(monkeypatch, *, password="managed-password", token="provider-token"):
    monkeypatch.setenv("ODYSSEUS_ADMIN_USER", " ManagedAdmin ")
    monkeypatch.setenv("ODYSSEUS_ADMIN_PASSWORD", password)
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_URL", "http://provider.test:7331/")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_NAME", "Managed inventory")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_TOKEN", token)
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_TIMEOUT", "12")


def _transport(expected_token="provider-token", *, status_code=200):
    def handler(request):
        assert request.url == httpx.URL("http://provider.test:7331/v1/artifacts")
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        if status_code != 200:
            return httpx.Response(status_code)
        return httpx.Response(
            200,
            json={
                "schema_version": 1,
                "provider": {"id": "managed-inventory"},
                "status": {"state": "ready", "sources": []},
                "artifacts": [],
            },
        )

    return httpx.MockTransport(handler)


def _unreachable_transport():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fresh_bootstrap_persists_encrypted_verified_configuration(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)

    result = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_transport()
    )

    assert result["configured"] is True
    assert result["verified"] is True
    assert result["source"] == "persisted"
    assert result["revision"]
    assert result["changed"] is True
    assert result["artifact_store"]["configured"] is True
    assert result["artifact_store"]["credential_present"] is True
    assert result["artifact_store"]["provider"] == "Managed inventory"
    assert result["artifact_store"]["verified"] is True
    assert result["artifact_store"]["verified_at"]

    auth = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert auth.verify_password("managedadmin", "managed-password") is True
    assert auth.is_admin("managedadmin") is True

    persisted_text = bootstrap_state["active"].read_text(encoding="utf-8")
    assert "provider-token" not in persisted_text
    assert "managed-password" not in persisted_text
    assert json.loads(persisted_text)["configuration"]["token"].startswith("enc:")
    assert not bootstrap_state["candidate"].exists()


@pytest.mark.asyncio
async def test_persisted_configuration_wins_as_a_whole_over_environment(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_URL", "http://wrong.test:9999")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_NAME", "Wrong environment")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_TOKEN", "wrong-token")
    resolved = bootstrap_state["config"].resolve_artifact_store_configuration()

    assert resolved is not None
    assert resolved.source == "persisted"
    assert resolved.base_url == "http://provider.test:7331"
    assert resolved.name == "Managed inventory"
    assert resolved.token == "provider-token"
    assert resolved.timeout_seconds == 12

    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    from src.cookbook_capabilities import cookbook_capabilities

    capability = cookbook_capabilities()["capabilities"]["artifact_store"]
    assert capability["list"] is True
    assert capability["provider"] == "Managed inventory"


@pytest.mark.asyncio
async def test_existing_admin_is_reconciled_without_touching_other_credentials(
    bootstrap_state, monkeypatch
):
    auth = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert auth.create_user("managedadmin", "old-password", is_admin=False)
    assert auth.create_user("other", "other-password", is_admin=True)
    auth._config["users"]["managedadmin"].update(
        {"totp_enabled": True, "totp_secret": "preserve-me"}
    )
    auth._save()
    old_session = auth.create_session("managedadmin", "old-password")
    assert old_session

    _set_inputs(monkeypatch, password="new-managed-password")
    result = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_transport()
    )

    reloaded = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert reloaded.verify_password("managedadmin", "new-managed-password") is True
    assert reloaded.verify_password("managedadmin", "old-password") is False
    assert reloaded.is_admin("managedadmin") is True
    assert reloaded.verify_password("other", "other-password") is True
    assert reloaded.users["managedadmin"]["totp_enabled"] is True
    assert reloaded.users["managedadmin"]["totp_secret"] == "preserve-me"
    assert reloaded.validate_token(old_session) is False
    assert result["sessions_revoked"] == 1

@pytest.mark.asyncio
async def test_password_is_not_changed_when_session_revocation_cannot_persist(
    bootstrap_state, monkeypatch
):
    auth = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert auth.create_user("managedadmin", "old-password", is_admin=True)
    assert auth.create_session("managedadmin", "old-password")
    _set_inputs(monkeypatch, password="new-managed-password")
    monkeypatch.setattr(bootstrap_state["auth_manager_cls"], "_save_sessions", lambda self: False)

    with pytest.raises(bootstrap_state["bootstrap"].ManagedBootstrapError) as exc:
        await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    assert "sessions could not be persisted" in str(exc.value)
    reloaded = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert reloaded.verify_password("managedadmin", "old-password") is True
    assert reloaded.verify_password("managedadmin", "new-managed-password") is False
    assert not bootstrap_state["active"].exists()
    assert not bootstrap_state["candidate"].exists()



@pytest.mark.asyncio
async def test_unreachable_first_provider_is_saved_unverified_with_warning(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)

    result = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_unreachable_transport()
    )

    active = bootstrap_state["config"].load_persisted_configuration()
    auth = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert result["configured"] is True
    assert result["verified"] is False
    assert "could not be verified" in result["warning"]
    assert active is not None
    assert active.verified is False
    assert active.token == "provider-token"
    assert auth.verify_password("managedadmin", "managed-password") is True
    assert not bootstrap_state["candidate"].exists()


@pytest.mark.asyncio
async def test_corrupt_existing_auth_is_not_replaced(
    bootstrap_state, monkeypatch
):
    original = b'{"users":'
    bootstrap_state["auth"].write_bytes(original)
    _set_inputs(monkeypatch)

    with pytest.raises(bootstrap_state["bootstrap"].ManagedBootstrapError) as exc:
        await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    assert "could not be read" in str(exc.value)
    assert bootstrap_state["auth"].read_bytes() == original
    assert not bootstrap_state["active"].exists()
    assert not bootstrap_state["candidate"].exists()


@pytest.mark.asyncio
async def test_unverified_replacement_activates_operator_supplied_state(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    _set_inputs(
        monkeypatch,
        password="replacement-password",
        token="replacement-token",
    )
    result = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_transport(expected_token="replacement-token", status_code=401)
    )

    active = bootstrap_state["config"].load_persisted_configuration()
    auth = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    assert active is not None
    assert active.revision != first["revision"]
    assert active.token == "replacement-token"
    assert active.verified is False
    assert result["verified"] is False
    assert "could not be verified" in result["warning"]
    assert auth.verify_password("managedadmin", "replacement-password") is True
    assert auth.verify_password("managedadmin", "managed-password") is False
    assert not bootstrap_state["candidate"].exists()


@pytest.mark.asyncio
async def test_same_unverified_configuration_can_be_verified_later(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_transport(status_code=401)
    )

    second = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    status = bootstrap_state["bootstrap"].bootstrap_status()

    assert second["changed"] is False
    assert second["revision"] == first["revision"] == status["revision"]
    assert second["verified"] is True
    assert "warning" not in second
    assert status["artifact_store"]["verified"] is True
    assert status["artifact_store"]["verified_at"]


@pytest.mark.asyncio
async def test_successful_noop_keeps_revision_and_status_redacts_secrets(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    second = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    status = bootstrap_state["bootstrap"].bootstrap_status()

    assert second["changed"] is False
    assert second["revision"] == first["revision"] == status["revision"]
    rendered = json.dumps(status)
    assert "provider-token" not in rendered
    assert "managed-password" not in rendered
    assert status["artifact_store"]["credential_present"] is True


@pytest.mark.asyncio
async def test_status_fails_closed_when_managed_admin_is_no_longer_admin(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    applied = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    auth = bootstrap_state["auth_manager_cls"](auth_path=str(bootstrap_state["auth"]))
    auth._config["users"]["managedadmin"]["is_admin"] = False
    auth._save()

    status = bootstrap_state["bootstrap"].bootstrap_status()

    assert status["revision"] == applied["revision"]
    assert status["verified"] is False
    assert status["managed_admin"]["exists"] is True
    assert status["managed_admin"]["is_admin"] is False


@pytest.mark.asyncio
async def test_bearer_value_beginning_with_encryption_marker_round_trips(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch, token="enc:real-provider-token")

    await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_transport(expected_token="enc:real-provider-token")
    )
    active = bootstrap_state["config"].load_persisted_configuration()

    assert active is not None
    assert active.token == "enc:real-provider-token"


def test_environment_configuration_remains_available_without_bootstrap(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)

    resolved = bootstrap_state["config"].resolve_artifact_store_configuration()

    assert resolved is not None
    assert resolved.source == "environment"
    assert resolved.base_url == "http://provider.test:7331"
    assert resolved.token == "provider-token"
