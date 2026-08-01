"""Local managed bootstrap for Outis authentication and ArtifactStore access.

This module is intentionally a local process interface, not an HTTP route.  A
deployment wrapper supplies secrets through the environment for one attended
run, then ordinary application starts use only the persistent managed state.
Run ``apply`` only while the main Outis application process is stopped so its
in-memory authentication state cannot diverge from the shared data directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from typing import Any

import httpx

from artifact_store.client import ArtifactStoreClient, ArtifactStoreError
from artifact_store.config import (
    ArtifactStoreConfiguration,
    ArtifactStoreConfigurationError,
    activate_candidate,
    discard_candidate,
    environment_configuration,
    load_candidate,
    load_persisted_configuration,
    same_provider_configuration,
    write_candidate,
)
from core.auth import AuthManager, validate_managed_admin_credentials
from src.constants import AUTH_FILE


STATUS_SCHEMA_VERSION = 1


class ManagedBootstrapError(RuntimeError):
    """The local bootstrap could not safely converge the requested state."""


def _status_for(configuration: ArtifactStoreConfiguration | None) -> dict[str, Any]:
    username = configuration.managed_admin_username if configuration else None
    auth = AuthManager(auth_path=AUTH_FILE)
    user = auth.users.get(username, {}) if username else {}
    managed_admin_ready = bool(user and user.get("is_admin"))
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "configured": bool(configuration),
        "verified": bool(
            configuration and configuration.verified and managed_admin_ready
        ),
        "source": configuration.source if configuration else None,
        "revision": configuration.revision if configuration else None,
        "managed_admin": {
            "username": username,
            "exists": bool(user),
            "is_admin": bool(user.get("is_admin")),
        },
        "artifact_store": {
            "configured": bool(configuration),
            "credential_present": bool(configuration and configuration.token),
            "provider": configuration.name if configuration else None,
            "verified": bool(configuration and configuration.verified),
            "verified_at": configuration.verified_at if configuration else None,
        },
    }


def bootstrap_status() -> dict[str, Any]:
    try:
        configuration = load_persisted_configuration()
    except ArtifactStoreConfigurationError as exc:
        raise ManagedBootstrapError(str(exc)) from exc
    return _status_for(configuration)


def _required_bootstrap_inputs() -> tuple[str, str, ArtifactStoreConfiguration]:
    username = os.getenv("ODYSSEUS_ADMIN_USER", "").strip()
    password = os.getenv("ODYSSEUS_ADMIN_PASSWORD", "")
    try:
        username = validate_managed_admin_credentials(username, password)
        configuration = environment_configuration()
    except (ValueError, ArtifactStoreConfigurationError) as exc:
        raise ManagedBootstrapError(str(exc)) from exc
    if configuration is None:
        raise ManagedBootstrapError("ArtifactStore URL is required for managed bootstrap")
    if not configuration.token:
        raise ManagedBootstrapError(
            "ArtifactStore bearer credential is required for managed bootstrap"
        )
    return username, password, configuration


async def apply_bootstrap(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    username, password, supplied = _required_bootstrap_inputs()
    write_candidate(supplied)
    try:
        candidate = load_candidate()
        try:
            current = load_persisted_configuration()
        except ArtifactStoreConfigurationError:
            # A structurally valid candidate may repair corrupt prior provider
            # state; the invalid document cannot be a working fallback.
            current = None

        provider_matches = same_provider_configuration(current, candidate)
        verified = bool(provider_matches and current and current.verified)
        verified_at = current.verified_at if verified and current else None
        verification_warning: str | None = None
        client = ArtifactStoreClient.from_configuration(candidate, transport=transport)
        if client is None:  # Defensive: a persisted candidate is always configured.
            raise ManagedBootstrapError("ArtifactStore candidate is missing")
        try:
            await client.list_artifacts()
        except ArtifactStoreError as exc:
            verification_warning = (
                "Provider configuration was saved but could not be verified: "
                f"{exc}"
            )
        else:
            verified = True
            if not (provider_matches and current and current.verified):
                verified_at = None

        auth = AuthManager(auth_path=AUTH_FILE)
        try:
            admin_result = auth.reconcile_managed_admin(username, password)
        except RuntimeError as exc:
            raise ManagedBootstrapError(str(exc)) from exc
        changed = (
            not provider_matches
            or admin_result.changed
            or (current is not None and current.managed_admin_username != username)
        )
        revision = (
            secrets.token_urlsafe(24)
            if changed or current is None or not current.revision
            else current.revision
        )
        active = activate_candidate(
            candidate,
            revision=revision,
            managed_admin_username=username,
            verified=verified,
            verified_at=verified_at,
        )
        result = _status_for(active)
        result.update(
            {
                "changed": changed,
                "sessions_revoked": admin_result.sessions_revoked,
            }
        )
        if verification_warning:
            result["warning"] = verification_warning
        return result
    finally:
        discard_candidate()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Converge or inspect local managed Outis bootstrap state."
    )
    parser.add_argument("command", choices=("apply", "status"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            asyncio.run(apply_bootstrap())
            if args.command == "apply"
            else bootstrap_status()
        )
    except (
        ManagedBootstrapError,
        ArtifactStoreConfigurationError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
