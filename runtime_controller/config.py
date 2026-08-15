"""Persistent and environment-backed RuntimeController configuration."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from core.atomic_io import atomic_write_json
from src.constants import RUNTIME_CONTROLLER_CANDIDATE_FILE, RUNTIME_CONTROLLER_CONFIG_FILE
from src.managed_transaction import ManagedTransactionError, load_transaction
from src.secret_storage import decrypt, encrypt

CONFIG_SCHEMA_VERSION = 1
DEFAULT_PROVIDER_NAME = "external-runtime-controller"
DEFAULT_TIMEOUT_SECONDS = 60.0
_TOKEN_PAYLOAD_PREFIX = "runtime-controller-token:"


class RuntimeControllerConfigurationError(RuntimeError):
    """Stored or supplied RuntimeController configuration is invalid."""


@dataclass(frozen=True)
class RuntimeControllerConfiguration:
    base_url: str
    token: str | None
    target: str
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
        raise RuntimeControllerConfigurationError("RuntimeController URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeControllerConfigurationError(
            "RuntimeController URL must be an absolute HTTP(S) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeControllerConfigurationError(
            "RuntimeController URL must not contain credentials, query, or fragment"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalized_timeout(value: object) -> float:
    try:
        timeout = float(value or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(0.5, min(timeout, 300.0))


def _validated_target(value: object) -> str:
    target = str(value or "").strip()
    if not target:
        raise RuntimeControllerConfigurationError("RuntimeController target is required")
    if any(character in target for character in ("/", "\\", "?", "#")):
        raise RuntimeControllerConfigurationError(
            "RuntimeController target must be one opaque path segment"
        )
    return target


def environment_configuration() -> RuntimeControllerConfiguration | None:
    base_url = os.getenv("OUTIS_RUNTIME_CONTROLLER_URL", "").strip()
    if not base_url:
        return None
    return RuntimeControllerConfiguration(
        base_url=validated_base_url(base_url),
        token=os.getenv("OUTIS_RUNTIME_CONTROLLER_TOKEN", "").strip() or None,
        target=_validated_target(os.getenv("OUTIS_RUNTIME_CONTROLLER_TARGET", "")),
        name=os.getenv("OUTIS_RUNTIME_CONTROLLER_NAME", DEFAULT_PROVIDER_NAME).strip()
        or DEFAULT_PROVIDER_NAME,
        timeout_seconds=normalized_timeout(
            os.getenv("OUTIS_RUNTIME_CONTROLLER_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        ),
    )


def _read_document(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeControllerConfigurationError(
            "RuntimeController configuration could not be read"
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise RuntimeControllerConfigurationError(
            "RuntimeController configuration has an unsupported schema"
        )
    return document


def _configuration_from_document(document: dict, *, source: str) -> RuntimeControllerConfiguration:
    entry = document.get("configuration")
    if not isinstance(entry, dict):
        raise RuntimeControllerConfigurationError("RuntimeController configuration is missing")
    payload = decrypt(str(entry.get("token") or ""))
    token = (
        payload[len(_TOKEN_PAYLOAD_PREFIX) :] if payload.startswith(_TOKEN_PAYLOAD_PREFIX) else None
    )
    try:
        transaction = load_transaction()
    except ManagedTransactionError as exc:
        raise RuntimeControllerConfigurationError(str(exc)) from exc
    return RuntimeControllerConfiguration(
        base_url=validated_base_url(str(entry.get("base_url") or "")),
        token=token or None,
        target=_validated_target(entry.get("target")),
        name=str(entry.get("name") or DEFAULT_PROVIDER_NAME).strip() or DEFAULT_PROVIDER_NAME,
        timeout_seconds=normalized_timeout(entry.get("timeout_seconds")),
        source=source,
        revision=transaction.revision if transaction else None,
        verified=bool(document.get("verified")),
        verified_at=str(document.get("verified_at") or "").strip() or None,
        managed_admin_username=transaction.managed_admin_username if transaction else None,
    )


def load_persisted_configuration() -> RuntimeControllerConfiguration | None:
    if not os.path.exists(RUNTIME_CONTROLLER_CONFIG_FILE):
        return None
    configuration = _configuration_from_document(
        _read_document(RUNTIME_CONTROLLER_CONFIG_FILE), source="persisted"
    )
    if not configuration.revision:
        raise RuntimeControllerConfigurationError(
            "Persisted RuntimeController configuration is missing its revision"
        )
    if not configuration.token:
        raise RuntimeControllerConfigurationError(
            "Persisted RuntimeController credential could not be read"
        )
    return configuration


def persisted_configuration_present() -> bool:
    return os.path.exists(RUNTIME_CONTROLLER_CONFIG_FILE)


def resolve_runtime_controller_configuration() -> RuntimeControllerConfiguration | None:
    if persisted_configuration_present():
        return load_persisted_configuration()
    return environment_configuration()


def write_candidate(configuration: RuntimeControllerConfiguration) -> None:
    if not configuration.token:
        raise RuntimeControllerConfigurationError(
            "RuntimeController bearer credential is required for managed bootstrap"
        )
    document = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "configuration": {
            "base_url": validated_base_url(configuration.base_url),
            "name": configuration.name or DEFAULT_PROVIDER_NAME,
            "target": _validated_target(configuration.target),
            "timeout_seconds": normalized_timeout(configuration.timeout_seconds),
            "token": encrypt(f"{_TOKEN_PAYLOAD_PREFIX}{configuration.token}"),
        },
    }
    atomic_write_json(RUNTIME_CONTROLLER_CANDIDATE_FILE, document, indent=2)


def load_candidate() -> RuntimeControllerConfiguration:
    configuration = _configuration_from_document(
        _read_document(RUNTIME_CONTROLLER_CANDIDATE_FILE), source="candidate"
    )
    if not configuration.token:
        raise RuntimeControllerConfigurationError(
            "RuntimeController bearer credential is required for managed bootstrap"
        )
    return configuration


def discard_candidate() -> None:
    try:
        Path(RUNTIME_CONTROLLER_CANDIDATE_FILE).unlink()
    except FileNotFoundError:
        pass


def same_provider_configuration(
    left: RuntimeControllerConfiguration | None,
    right: RuntimeControllerConfiguration,
) -> bool:
    return bool(
        left
        and left.base_url == right.base_url
        and left.name == right.name
        and left.target == right.target
        and left.timeout_seconds == right.timeout_seconds
        and secrets.compare_digest(left.token or "", right.token or "")
    )


def activate_candidate(
    configuration: RuntimeControllerConfiguration,
    *,
    revision: str,
    managed_admin_username: str,
    verified: bool,
    verified_at: str | None = None,
) -> RuntimeControllerConfiguration:
    if verified and not verified_at:
        verified_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if not verified:
        verified_at = None
    document = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "verified": verified,
        "verified_at": verified_at,
        "configuration": {
            "base_url": validated_base_url(configuration.base_url),
            "name": configuration.name or DEFAULT_PROVIDER_NAME,
            "target": _validated_target(configuration.target),
            "timeout_seconds": normalized_timeout(configuration.timeout_seconds),
            "token": encrypt(f"{_TOKEN_PAYLOAD_PREFIX}{configuration.token or ''}"),
        },
    }
    atomic_write_json(RUNTIME_CONTROLLER_CONFIG_FILE, document, indent=2)
    discard_candidate()
    return replace(
        configuration,
        source="persisted",
        revision=revision,
        verified=verified,
        verified_at=verified_at,
        managed_admin_username=managed_admin_username,
    )
