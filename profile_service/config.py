"""Persistent and environment-backed ProfileService configuration.

Mirrors ``artifact_store/config.py``. Standalone deployments may continue to
configure the provider through the environment. A bootstrapped persistent
configuration is authoritative as a whole: callers never combine its fields
with environment values. The active file records whether the provider has ever
been reached successfully; an unverified configuration remains usable so
optional provider downtime cannot block Outis configuration or startup.

ArtifactStore and ProfileService are independent roles. A deployment may
deliberately point both at the same endpoint with the same bearer, but neither
role is ever inferred from the other and their configuration sources are never
merged.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from core.atomic_io import atomic_write_json
from src.constants import PROFILE_SERVICE_CANDIDATE_FILE, PROFILE_SERVICE_CONFIG_FILE
from src.managed_transaction import ManagedTransactionError, load_transaction
from src.secret_storage import decrypt, encrypt


CONFIG_SCHEMA_VERSION = 1
DEFAULT_PROVIDER_NAME = "external-profile-service"
DEFAULT_TIMEOUT_SECONDS = 10.0
_TOKEN_PAYLOAD_PREFIX = "profile-service-token:"


class ProfileServiceConfigurationError(RuntimeError):
    """Stored or supplied ProfileService configuration is invalid."""


@dataclass(frozen=True)
class ProfileServiceConfiguration:
    base_url: str
    token: str | None
    name: str = DEFAULT_PROVIDER_NAME
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    source: str = "environment"
    revision: str | None = None
    verified: bool = False
    verified_at: str | None = None
    managed_admin_username: str | None = None


def validated_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ProfileServiceConfigurationError("ProfileService URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProfileServiceConfigurationError(
            "ProfileService URL must be an absolute HTTP(S) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProfileServiceConfigurationError(
            "ProfileService URL must not contain credentials, query, or fragment"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalized_timeout(value: object) -> float:
    try:
        timeout = float(value or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(0.5, min(timeout, 60.0))


def environment_configuration() -> ProfileServiceConfiguration | None:
    base_url = os.getenv("OUTIS_PROFILE_SERVICE_URL", "").strip()
    if not base_url:
        return None
    return ProfileServiceConfiguration(
        base_url=validated_base_url(base_url),
        token=os.getenv("OUTIS_PROFILE_SERVICE_TOKEN", "").strip() or None,
        name=os.getenv("OUTIS_PROFILE_SERVICE_NAME", DEFAULT_PROVIDER_NAME).strip()
        or DEFAULT_PROVIDER_NAME,
        timeout_seconds=normalized_timeout(
            os.getenv("OUTIS_PROFILE_SERVICE_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        ),
    )


def _read_document(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ProfileServiceConfigurationError(
            "ProfileService configuration could not be read"
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ProfileServiceConfigurationError(
            "ProfileService configuration has an unsupported schema"
        )
    return document


def _encrypt_token(token: str) -> str:
    # ``secret_storage.encrypt`` treats values beginning with ``enc:`` as
    # already encrypted. Prefixing the plaintext payload keeps arbitrary bearer
    # values (including a real token beginning ``enc:``) unambiguous. The prefix
    # is role-specific, so an ArtifactStore payload cannot be read back here.
    return encrypt(f"{_TOKEN_PAYLOAD_PREFIX}{token}")


def _decrypt_token(value: object) -> str | None:
    payload = decrypt(str(value or ""))
    if not payload.startswith(_TOKEN_PAYLOAD_PREFIX):
        return None
    return payload[len(_TOKEN_PAYLOAD_PREFIX) :] or None


def _configuration_from_document(document: dict, *, source: str) -> ProfileServiceConfiguration:
    entry = document.get("configuration")
    if not isinstance(entry, dict):
        raise ProfileServiceConfigurationError("ProfileService configuration is missing")
    token = _decrypt_token(entry.get("token"))
    # The revision and the managed administrator describe the bootstrap run,
    # not this role, so they come from the transaction record shared with
    # ArtifactStore.
    try:
        transaction = load_transaction()
    except ManagedTransactionError as exc:
        raise ProfileServiceConfigurationError(str(exc)) from exc
    return ProfileServiceConfiguration(
        base_url=validated_base_url(str(entry.get("base_url") or "")),
        token=token,
        name=str(entry.get("name") or DEFAULT_PROVIDER_NAME).strip()
        or DEFAULT_PROVIDER_NAME,
        timeout_seconds=normalized_timeout(entry.get("timeout_seconds")),
        source=source,
        revision=transaction.revision if transaction else None,
        verified=bool(document.get("verified")),
        verified_at=str(document.get("verified_at") or "").strip() or None,
        managed_admin_username=(
            transaction.managed_admin_username if transaction else None
        ),
    )


def load_persisted_configuration() -> ProfileServiceConfiguration | None:
    if not os.path.exists(PROFILE_SERVICE_CONFIG_FILE):
        return None
    configuration = _configuration_from_document(
        _read_document(PROFILE_SERVICE_CONFIG_FILE), source="persisted"
    )
    if not configuration.revision:
        raise ProfileServiceConfigurationError(
            "Persisted ProfileService configuration is missing its revision"
        )
    if not configuration.token:
        raise ProfileServiceConfigurationError(
            "Persisted ProfileService credential could not be read"
        )
    return configuration


def persisted_configuration_present() -> bool:
    return os.path.exists(PROFILE_SERVICE_CONFIG_FILE)


def resolve_profile_service_configuration() -> ProfileServiceConfiguration | None:
    """Resolve one complete source, with managed persistence taking priority."""
    if os.path.exists(PROFILE_SERVICE_CONFIG_FILE):
        return load_persisted_configuration()
    return environment_configuration()


def write_candidate(configuration: ProfileServiceConfiguration) -> None:
    if not configuration.token:
        raise ProfileServiceConfigurationError(
            "ProfileService bearer credential is required for managed bootstrap"
        )
    document = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "configuration": {
            "base_url": validated_base_url(configuration.base_url),
            "name": configuration.name or DEFAULT_PROVIDER_NAME,
            "timeout_seconds": normalized_timeout(configuration.timeout_seconds),
            "token": _encrypt_token(configuration.token),
        },
    }
    atomic_write_json(PROFILE_SERVICE_CANDIDATE_FILE, document, indent=2)


def load_candidate() -> ProfileServiceConfiguration:
    configuration = _configuration_from_document(
        _read_document(PROFILE_SERVICE_CANDIDATE_FILE), source="candidate"
    )
    if not configuration.token:
        raise ProfileServiceConfigurationError(
            "ProfileService bearer credential is required for managed bootstrap"
        )
    return configuration


def discard_candidate() -> None:
    try:
        Path(PROFILE_SERVICE_CANDIDATE_FILE).unlink()
    except FileNotFoundError:
        pass


def same_provider_configuration(
    left: ProfileServiceConfiguration | None,
    right: ProfileServiceConfiguration,
) -> bool:
    if left is None:
        return False
    return (
        left.base_url == right.base_url
        and left.name == right.name
        and left.timeout_seconds == right.timeout_seconds
        and secrets.compare_digest(left.token or "", right.token or "")
    )


def activate_candidate(
    configuration: ProfileServiceConfiguration,
    *,
    revision: str,
    managed_admin_username: str,
    verified: bool,
    verified_at: str | None = None,
) -> ProfileServiceConfiguration:
    if verified and not verified_at:
        verified_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not verified:
        verified_at = None
    # ``revision`` and ``managed_admin_username`` are deliberately not written
    # here: they belong to the transaction record, which the caller commits
    # once for every role it converged.
    document = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "verified": verified,
        "verified_at": verified_at,
        "configuration": {
            "base_url": validated_base_url(configuration.base_url),
            "name": configuration.name or DEFAULT_PROVIDER_NAME,
            "timeout_seconds": normalized_timeout(configuration.timeout_seconds),
            "token": _encrypt_token(configuration.token or ""),
        },
    }
    atomic_write_json(PROFILE_SERVICE_CONFIG_FILE, document, indent=2)
    discard_candidate()
    return replace(
        configuration,
        source="persisted",
        revision=revision,
        verified=verified,
        verified_at=verified_at,
        managed_admin_username=managed_admin_username,
    )
