"""Local managed bootstrap for Outis authentication and provider access.

This module is intentionally a local process interface, not an HTTP route.  A
deployment wrapper supplies secrets through the environment for one attended
run, then ordinary application starts use only the persistent managed state.
Run ``apply`` only while the main Outis application process is stopped so its
in-memory authentication state cannot diverge from the shared data directory.

A run converges one or more independent provider roles as a single
transaction under a single revision. ArtifactStore is required; ProfileService
is optional and additive. A run that supplies no ProfileService inputs does
not address that role at all: existing ProfileService state is left exactly as
it was and contributes nothing to whether the revision changes, so an
ArtifactStore-only deployment behaves identically whether or not a
ProfileService was ever configured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

import artifact_store.config as artifact_store_config
import profile_service.config as profile_service_config
from artifact_store.client import ArtifactStoreClient, ArtifactStoreError
from artifact_store.config import ArtifactStoreConfigurationError
from core.auth import AuthManager, validate_managed_admin_credentials
from profile_service.client import ProfileServiceClient, ProfileServiceError
from profile_service.config import ProfileServiceConfigurationError
from src.constants import AUTH_FILE
from src.managed_transaction import (
    ManagedTransactionError,
    load_transaction,
    write_transaction,
)


STATUS_SCHEMA_VERSION = 1


class ManagedBootstrapError(RuntimeError):
    """The local bootstrap could not safely converge the requested state."""


@dataclass(frozen=True)
class _Role:
    """One independently selectable provider role within a bootstrap run."""

    key: str
    label: str
    config: Any
    configuration_error: type[Exception]
    client_error: type[Exception]
    build_client: Callable[[Any, httpx.AsyncBaseTransport | None], Any]
    probe: Callable[[Any], Awaitable[Any]]
    required: bool


ARTIFACT_STORE_ROLE = _Role(
    key="artifact_store",
    label="ArtifactStore",
    config=artifact_store_config,
    configuration_error=ArtifactStoreConfigurationError,
    client_error=ArtifactStoreError,
    build_client=lambda configuration, transport: ArtifactStoreClient.from_configuration(
        configuration, transport=transport
    ),
    probe=lambda client: client.list_artifacts(),
    required=True,
)

PROFILE_SERVICE_ROLE = _Role(
    key="profile_service",
    label="ProfileService",
    config=profile_service_config,
    configuration_error=ProfileServiceConfigurationError,
    client_error=ProfileServiceError,
    build_client=lambda configuration, transport: ProfileServiceClient.from_configuration(
        configuration, transport=transport
    ),
    # Discovery is the cheapest call that proves both reachability and that the
    # service speaks a contract version Outis supports.
    probe=lambda client: client.get_service(),
    required=False,
)

ROLES = (ARTIFACT_STORE_ROLE, PROFILE_SERVICE_ROLE)


@dataclass
class _RoleOutcome:
    role: _Role
    addressed: bool = False
    configuration: Any | None = None
    changed: bool = False
    warning: str | None = None
    verified_at: str | None = None
    verified: bool = False


def _role_status(configuration: Any | None) -> dict[str, Any]:
    return {
        "configured": bool(configuration),
        "credential_present": bool(configuration and configuration.token),
        "provider": configuration.name if configuration else None,
        "verified": bool(configuration and configuration.verified),
        "verified_at": configuration.verified_at if configuration else None,
    }


def _load_role_configuration(role: _Role) -> Any | None:
    try:
        return role.config.load_persisted_configuration()
    except role.configuration_error as exc:
        raise ManagedBootstrapError(str(exc)) from exc


def _status_for(configurations: dict[str, Any]) -> dict[str, Any]:
    try:
        transaction = load_transaction()
    except ManagedTransactionError as exc:
        raise ManagedBootstrapError(str(exc)) from exc
    username = transaction.managed_admin_username if transaction else None
    auth = AuthManager(auth_path=AUTH_FILE)
    user = auth.users.get(username, {}) if username else {}
    managed_admin_ready = bool(user and user.get("is_admin"))

    present = [configurations.get(role.key) for role in ROLES]
    configured_roles = [configuration for configuration in present if configuration]
    status = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "configured": bool(configured_roles),
        # Every role this deployment has configured must be reachable before
        # the bootstrap as a whole counts as verified. A role that was never
        # configured is not a reason to withhold verification.
        "verified": bool(
            configured_roles
            and all(configuration.verified for configuration in configured_roles)
            and managed_admin_ready
        ),
        "source": "persisted" if transaction else None,
        "revision": transaction.revision if transaction else None,
        "managed_admin": {
            "username": username,
            "exists": bool(user),
            "is_admin": bool(user.get("is_admin")),
        },
    }
    for role in ROLES:
        status[role.key] = _role_status(configurations.get(role.key))
    return status


def bootstrap_status() -> dict[str, Any]:
    return _status_for(
        {role.key: _load_role_configuration(role) for role in ROLES}
    )


def _managed_admin_inputs() -> tuple[str, str]:
    username = os.getenv("ODYSSEUS_ADMIN_USER", "").strip()
    password = os.getenv("ODYSSEUS_ADMIN_PASSWORD", "")
    try:
        username = validate_managed_admin_credentials(username, password)
    except ValueError as exc:
        raise ManagedBootstrapError(str(exc)) from exc
    return username, password


def _supplied_configuration(role: _Role) -> Any | None:
    """Read this run's inputs for a role, or None when it is not addressed."""
    try:
        configuration = role.config.environment_configuration()
    except role.configuration_error as exc:
        raise ManagedBootstrapError(str(exc)) from exc
    if configuration is None:
        if role.required:
            raise ManagedBootstrapError(
                f"{role.label} URL is required for managed bootstrap"
            )
        return None
    if not configuration.token:
        raise ManagedBootstrapError(
            f"{role.label} bearer credential is required for managed bootstrap"
        )
    return configuration


async def _converge_role(
    role: _Role,
    supplied: Any,
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[_RoleOutcome, Any]:
    """Verify a role's supplied inputs and decide whether it changed.

    Returns the outcome and the loaded candidate. Nothing is activated here:
    activation happens only once every role has been converged, so a single
    revision covers them all.
    """
    outcome = _RoleOutcome(role=role, addressed=True)
    role.config.write_candidate(supplied)
    candidate = role.config.load_candidate()
    try:
        current = role.config.load_persisted_configuration()
    except role.configuration_error:
        # A structurally valid candidate may repair corrupt prior provider
        # state; the invalid document cannot be a working fallback.
        current = None

    provider_matches = role.config.same_provider_configuration(current, candidate)
    outcome.verified = bool(provider_matches and current and current.verified)
    outcome.verified_at = current.verified_at if outcome.verified and current else None

    client = role.build_client(candidate, transport)
    if client is None:  # Defensive: a persisted candidate is always configured.
        raise ManagedBootstrapError(f"{role.label} candidate is missing")
    try:
        await role.probe(client)
    except role.client_error as exc:
        outcome.warning = (
            f"{role.label} configuration was saved but could not be verified: {exc}"
        )
    else:
        outcome.verified = True
        if not (provider_matches and current and current.verified):
            outcome.verified_at = None

    outcome.changed = not provider_matches
    return outcome, candidate


async def apply_bootstrap(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    username, password = _managed_admin_inputs()
    supplied = {role.key: _supplied_configuration(role) for role in ROLES}
    addressed = [role for role in ROLES if supplied[role.key] is not None]

    try:
        outcomes: dict[str, _RoleOutcome] = {}
        candidates: dict[str, Any] = {}
        for role in addressed:
            outcomes[role.key], candidates[role.key] = await _converge_role(
                role, supplied[role.key], transport=transport
            )

        try:
            transaction = load_transaction()
        except ManagedTransactionError:
            # An unreadable transaction record cannot pin a revision; this run
            # mints a fresh one rather than refusing to converge.
            transaction = None

        auth = AuthManager(auth_path=AUTH_FILE)
        try:
            admin_result = auth.reconcile_managed_admin(username, password)
        except RuntimeError as exc:
            raise ManagedBootstrapError(str(exc)) from exc

        # Only roles this run addressed can report a change. A role left
        # untouched contributes nothing, so an ArtifactStore-only run never
        # bumps the revision on account of ProfileService state that is
        # present, absent, or newly appearing.
        changed = (
            any(outcomes[role.key].changed for role in addressed)
            or admin_result.changed
            or (
                transaction is not None
                and transaction.managed_admin_username != username
            )
        )
        revision = (
            secrets.token_urlsafe(24)
            if changed or transaction is None or not transaction.revision
            else transaction.revision
        )

        configurations: dict[str, Any] = {}
        for role in ROLES:
            outcome = outcomes.get(role.key)
            if outcome is None:
                # Not addressed by this run: its managed state, if any, stands
                # untouched and is reported as it is on disk.
                configurations[role.key] = _load_role_configuration(role)
                continue
            configurations[role.key] = role.config.activate_candidate(
                candidates[role.key],
                revision=revision,
                managed_admin_username=username,
                verified=outcome.verified,
                verified_at=outcome.verified_at,
            )

        # The transaction record is the commit point: every role document is
        # in place before the revision that describes them is published.
        write_transaction(revision=revision, managed_admin_username=username)

        result = _status_for(configurations)
        result.update(
            {
                "changed": changed,
                "sessions_revoked": admin_result.sessions_revoked,
            }
        )
        warnings = [
            outcomes[role.key].warning for role in addressed if outcomes[role.key].warning
        ]
        if warnings:
            result["warning"] = " ".join(warnings)
        return result
    finally:
        for role in ROLES:
            role.config.discard_candidate()


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
        ManagedTransactionError,
        ArtifactStoreConfigurationError,
        ProfileServiceConfigurationError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
