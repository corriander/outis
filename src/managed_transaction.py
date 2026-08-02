"""The managed bootstrap transaction record.

A bootstrap run converges several independent provider roles at once. The
revision and the managed administrator identify that run as a whole, so they
belong to the transaction rather than to any one role: a deployment may
configure ArtifactStore alone, ProfileService alone, or both, and there is
still exactly one revision describing the state those roles were last
converged into.

Before ProfileService existed, these two fields were written inline in
``artifact_store.json`` because it was the only role. Deployments bootstrapped
by that version are read through the legacy fallback below, which treats the
inline fields as the transaction record. The next ``apply`` writes the split
form and the fallback stops being consulted. Nothing rewrites the old file
merely because it was read.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from core.atomic_io import atomic_write_json
from src.constants import ARTIFACT_STORE_CONFIG_FILE, MANAGED_BOOTSTRAP_FILE


TRANSACTION_SCHEMA_VERSION = 1


class ManagedTransactionError(RuntimeError):
    """The stored managed bootstrap transaction record is unusable."""


@dataclass(frozen=True)
class ManagedTransaction:
    revision: str
    managed_admin_username: str | None = None


def _read_document(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ManagedTransactionError(
            "Managed bootstrap transaction could not be read"
        ) from exc
    if not isinstance(document, dict):
        raise ManagedTransactionError(
            "Managed bootstrap transaction has an unsupported schema"
        )
    return document


def _transaction_from_document(document: dict) -> ManagedTransaction | None:
    revision = str(document.get("revision") or "").strip()
    if not revision:
        return None
    username = str(document.get("managed_admin_username") or "").strip().lower() or None
    return ManagedTransaction(revision=revision, managed_admin_username=username)


def _legacy_transaction() -> ManagedTransaction | None:
    """Adopt the pre-split inline fields from the ArtifactStore document.

    A malformed legacy file is not raised here: the ArtifactStore config
    resolver reports that fault itself, with a message naming the role.
    """
    if not os.path.exists(ARTIFACT_STORE_CONFIG_FILE):
        return None
    try:
        document = _read_document(ARTIFACT_STORE_CONFIG_FILE)
    except ManagedTransactionError:
        return None
    return _transaction_from_document(document)


def load_transaction() -> ManagedTransaction | None:
    if not os.path.exists(MANAGED_BOOTSTRAP_FILE):
        return _legacy_transaction()
    document = _read_document(MANAGED_BOOTSTRAP_FILE)
    if document.get("schema_version") != TRANSACTION_SCHEMA_VERSION:
        raise ManagedTransactionError(
            "Managed bootstrap transaction has an unsupported schema"
        )
    transaction = _transaction_from_document(document)
    if transaction is None:
        raise ManagedTransactionError(
            "Managed bootstrap transaction is missing its revision"
        )
    return transaction


def transaction_present() -> bool:
    return os.path.exists(MANAGED_BOOTSTRAP_FILE) or _legacy_transaction() is not None


def write_transaction(
    *, revision: str, managed_admin_username: str
) -> ManagedTransaction:
    """Commit the transaction record.

    Written last in an ``apply`` run: until it lands the run has not committed,
    and a role document without a transaction is reported as a configuration
    fault rather than silently treated as unconfigured.
    """
    revision = str(revision or "").strip()
    if not revision:
        raise ManagedTransactionError("A managed bootstrap revision is required")
    document = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "revision": revision,
        "managed_admin_username": managed_admin_username,
    }
    atomic_write_json(MANAGED_BOOTSTRAP_FILE, document, indent=2)
    return ManagedTransaction(
        revision=revision,
        managed_admin_username=(managed_admin_username or "").strip().lower() or None,
    )
