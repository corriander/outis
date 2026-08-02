import json

import httpx
import pytest


@pytest.fixture
def bootstrap_state(tmp_path, monkeypatch):
    import artifact_store.config as config
    import profile_service.config as profile_config
    import src.managed_bootstrap as bootstrap
    import src.managed_transaction as managed_transaction
    import src.secret_storage as secret_storage

    auth_path = tmp_path / "auth.json"
    active_path = tmp_path / "artifact_store.json"
    candidate_path = tmp_path / "artifact_store.pending.json"
    profile_active_path = tmp_path / "profile_service.json"
    profile_candidate_path = tmp_path / "profile_service.pending.json"
    transaction_path = tmp_path / "managed_bootstrap.json"
    key_path = tmp_path / ".app_key"

    monkeypatch.setattr(config, "ARTIFACT_STORE_CONFIG_FILE", str(active_path))
    monkeypatch.setattr(config, "ARTIFACT_STORE_CANDIDATE_FILE", str(candidate_path))
    monkeypatch.setattr(
        profile_config, "PROFILE_SERVICE_CONFIG_FILE", str(profile_active_path)
    )
    monkeypatch.setattr(
        profile_config, "PROFILE_SERVICE_CANDIDATE_FILE", str(profile_candidate_path)
    )
    monkeypatch.setattr(
        managed_transaction, "MANAGED_BOOTSTRAP_FILE", str(transaction_path)
    )
    monkeypatch.setattr(
        managed_transaction, "ARTIFACT_STORE_CONFIG_FILE", str(active_path)
    )
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
        "OUTIS_PROFILE_SERVICE_URL",
        "OUTIS_PROFILE_SERVICE_NAME",
        "OUTIS_PROFILE_SERVICE_TOKEN",
        "OUTIS_PROFILE_SERVICE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)

    return {
        "auth": auth_path,
        "active": active_path,
        "candidate": candidate_path,
        "profile_active": profile_active_path,
        "profile_candidate": profile_candidate_path,
        "transaction": transaction_path,
        "key": key_path,
        "config": config,
        "profile_config": profile_config,
        "bootstrap": bootstrap,
        "managed_transaction": managed_transaction,
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


# -- two independent roles under one transaction ---------------------------

PROFILE_DISCOVERY = {
    "schema_version": 1,
    "service_id": "managed-profile-service",
    "contract_version": 1,
    "accepted_authorities": ["managed-inventory"],
    "profiles": {"concurrency": {"etag": True, "if_match_required_on_write": True}},
    "auth": {"mode": "bearer"},
    "form": {"url": "/v1/form"},
}


def _set_profile_inputs(monkeypatch, *, token="profile-token"):
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", "http://profiles.test:7332/")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_NAME", "Managed profiles")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TOKEN", token)
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TIMEOUT", "12")


def _both_roles_transport(*, profile_status=200):
    """One transport serving both provider roles, routed by host.

    ``apply_bootstrap`` hands the same transport to every role it converges,
    so a two-role run needs a handler that answers both probes.
    """

    def handler(request):
        if request.url.host == "profiles.test":
            assert request.url.path == "/v1/service"
            assert request.headers.get("Authorization") == "Bearer profile-token"
            if profile_status != 200:
                return httpx.Response(profile_status)
            return httpx.Response(200, json=PROFILE_DISCOVERY)
        assert request.url == httpx.URL("http://provider.test:7331/v1/artifacts")
        assert request.headers.get("Authorization") == "Bearer provider-token"
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


@pytest.mark.asyncio
async def test_both_roles_are_converged_under_one_revision(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)

    result = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )

    assert result["configured"] is True
    assert result["verified"] is True
    assert result["artifact_store"]["provider"] == "Managed inventory"
    assert result["artifact_store"]["verified"] is True
    assert result["profile_service"]["provider"] == "Managed profiles"
    assert result["profile_service"]["verified"] is True
    assert result["profile_service"]["credential_present"] is True

    # One revision describes both roles, and it lives in the transaction
    # record rather than in either role document.
    transaction = json.loads(bootstrap_state["transaction"].read_text(encoding="utf-8"))
    assert transaction["revision"] == result["revision"]
    assert transaction["managed_admin_username"] == "managedadmin"

    artifact_document = json.loads(bootstrap_state["active"].read_text(encoding="utf-8"))
    profile_document = json.loads(
        bootstrap_state["profile_active"].read_text(encoding="utf-8")
    )
    for document in (artifact_document, profile_document):
        assert "revision" not in document
        assert "managed_admin_username" not in document

    profile_text = bootstrap_state["profile_active"].read_text(encoding="utf-8")
    assert "profile-token" not in profile_text
    assert profile_document["configuration"]["token"].startswith("enc:")
    assert not bootstrap_state["profile_candidate"].exists()


@pytest.mark.asyncio
async def test_an_artifact_store_only_rerun_does_not_disturb_profile_service(
    bootstrap_state, monkeypatch
):
    """The constraint this whole split exists to protect.

    A deployment that has both roles, re-run by a wrapper that supplies only
    ArtifactStore inputs, must leave the ProfileService document byte-identical
    and must not bump the shared revision.
    """
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )
    profile_before = bootstrap_state["profile_active"].read_bytes()

    for name in (
        "OUTIS_PROFILE_SERVICE_URL",
        "OUTIS_PROFILE_SERVICE_NAME",
        "OUTIS_PROFILE_SERVICE_TOKEN",
        "OUTIS_PROFILE_SERVICE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)

    second = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    assert second["changed"] is False
    assert second["revision"] == first["revision"]
    assert bootstrap_state["profile_active"].read_bytes() == profile_before
    # The untouched role is still reported, read from disk as it stands.
    assert second["profile_service"]["configured"] is True
    assert second["profile_service"]["provider"] == "Managed profiles"
    assert second["profile_service"]["verified"] is True


@pytest.mark.asyncio
async def test_an_artifact_store_only_deployment_is_unaffected_by_the_second_role(
    bootstrap_state, monkeypatch
):
    """ProfileService never having existed is not a change either."""
    _set_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    second = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["revision"] == first["revision"]
    assert second["profile_service"]["configured"] is False
    assert second["profile_service"]["provider"] is None
    assert second["verified"] is True
    assert not bootstrap_state["profile_active"].exists()


@pytest.mark.asyncio
async def test_adding_profile_service_later_changes_the_revision(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    _set_profile_inputs(monkeypatch)
    second = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )

    assert second["changed"] is True
    assert second["revision"] != first["revision"]
    assert second["profile_service"]["configured"] is True

    # ...and converging that same pair again is inert.
    third = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )
    assert third["changed"] is False
    assert third["revision"] == second["revision"]


@pytest.mark.asyncio
async def test_an_unreachable_profile_service_is_saved_unverified_with_a_warning(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)

    result = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport(profile_status=503)
    )

    assert result["configured"] is True
    assert result["artifact_store"]["verified"] is True
    assert result["profile_service"]["configured"] is True
    assert result["profile_service"]["verified"] is False
    assert "ProfileService" in result["warning"]
    assert "could not be verified" in result["warning"]
    # One role failing verification withholds the overall verified flag, but
    # the operator's configuration is still saved.
    assert result["verified"] is False
    assert bootstrap_state["profile_active"].exists()


@pytest.mark.asyncio
async def test_a_later_successful_verification_retains_the_revision(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport(profile_status=503)
    )
    assert first["profile_service"]["verified"] is False

    second = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )

    assert second["profile_service"]["verified"] is True
    assert second["changed"] is False
    assert second["revision"] == first["revision"]


@pytest.mark.asyncio
async def test_an_unchanged_role_stays_verified_when_the_provider_is_down(
    bootstrap_state, monkeypatch
):
    """`verified` means ever-reached for this config, not reachable right now.

    A wrapper that re-runs apply on every deploy must not downgrade a healthy
    deployment to unverified just because the provider happened to be
    restarting. Provider availability is runtime health; this flag is
    bootstrap state.
    """
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)
    first = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )
    assert first["verified"] is True
    first_verified_at = first["profile_service"]["verified_at"]
    assert first_verified_at

    second = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_unreachable_transport()
    )

    assert second["verified"] is True
    assert second["artifact_store"]["verified"] is True
    assert second["profile_service"]["verified"] is True
    # The original timestamp stands; this run proved nothing new.
    assert second["profile_service"]["verified_at"] == first_verified_at
    assert second["changed"] is False
    assert second["revision"] == first["revision"]
    assert "could not be verified" in second["warning"]


@pytest.mark.asyncio
async def test_changed_inputs_start_verification_again_from_false(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)
    await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )

    # A different bearer is different configuration: the prior success says
    # nothing about it, so it cannot inherit that verification.
    _set_profile_inputs(monkeypatch, token="rotated-profile-token")
    second = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_unreachable_transport()
    )

    assert second["profile_service"]["verified"] is False
    assert second["profile_service"]["verified_at"] is None
    assert second["verified"] is False
    assert second["changed"] is True


@pytest.mark.asyncio
async def test_status_reports_both_roles_without_reconverging(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    _set_profile_inputs(monkeypatch)
    applied = await bootstrap_state["bootstrap"].apply_bootstrap(
        transport=_both_roles_transport()
    )

    status = bootstrap_state["bootstrap"].bootstrap_status()

    assert status["revision"] == applied["revision"]
    assert status["source"] == "persisted"
    assert status["managed_admin"]["username"] == "managedadmin"
    assert status["managed_admin"]["is_admin"] is True
    assert status["artifact_store"]["provider"] == "Managed inventory"
    assert status["profile_service"]["provider"] == "Managed profiles"
    assert status["verified"] is True


# -- migration from the pre-split single-role layout ------------------------


def _rewrite_as_legacy(bootstrap_state, *, revision, username="managedadmin"):
    """Recreate the layout written before ProfileService existed.

    The revision and managed administrator lived inline in the ArtifactStore
    document; there was no transaction record.
    """
    document = json.loads(bootstrap_state["active"].read_text(encoding="utf-8"))
    document["revision"] = revision
    document["managed_admin_username"] = username
    bootstrap_state["active"].write_text(json.dumps(document, indent=2), encoding="utf-8")
    bootstrap_state["transaction"].unlink()


@pytest.mark.asyncio
async def test_a_pre_split_deployment_is_read_without_being_rewritten(
    bootstrap_state, monkeypatch
):
    _set_inputs(monkeypatch)
    applied = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    _rewrite_as_legacy(bootstrap_state, revision=applied["revision"])
    legacy_bytes = bootstrap_state["active"].read_bytes()

    status = bootstrap_state["bootstrap"].bootstrap_status()

    assert status["revision"] == applied["revision"]
    assert status["managed_admin"]["username"] == "managedadmin"
    assert status["artifact_store"]["provider"] == "Managed inventory"
    # Reading legacy state does not migrate it.
    assert bootstrap_state["active"].read_bytes() == legacy_bytes
    assert not bootstrap_state["transaction"].exists()


@pytest.mark.asyncio
async def test_a_pre_split_revision_survives_an_input_identical_rerun(
    bootstrap_state, monkeypatch
):
    """Upgrading Outis must not look like a configuration change.

    The wrapper re-runs apply with the same inputs after the upgrade; the
    revision it already attested to has to survive, or every deployment would
    appear to have drifted the moment it upgraded.
    """
    _set_inputs(monkeypatch)
    applied = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())
    _rewrite_as_legacy(bootstrap_state, revision=applied["revision"])

    rerun = await bootstrap_state["bootstrap"].apply_bootstrap(transport=_transport())

    assert rerun["changed"] is False
    assert rerun["revision"] == applied["revision"]
    # The rerun writes the split layout, and the inline fields are gone.
    assert bootstrap_state["transaction"].exists()
    document = json.loads(bootstrap_state["active"].read_text(encoding="utf-8"))
    assert "revision" not in document
    assert "managed_admin_username" not in document
