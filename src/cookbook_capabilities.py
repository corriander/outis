"""Deployment capability boundary for the Cookbook.

Outis is a constructive fork: inherited features stay in place until an
enhanced, provider-backed replacement exists. The default mode is therefore
``native`` — the full inherited Odysseus Cookbook. ``external`` declares a
deployment where acquisition, profiles, and runtime lifecycle are owned by
external providers; until those providers exist it is a deliberately reduced
catalogue-only surface, not the recommended configuration.
"""

import os

from fastapi import HTTPException


_NATIVE_VALUES = {"native", "odysseus"}


def cookbook_capabilities() -> dict:
    raw_mode = os.getenv("OUTIS_COOKBOOK_MODE", "native").strip().lower()
    native = raw_mode in _NATIVE_VALUES
    mode = "native" if native else "external"
    provider = "odysseus-native" if native else None

    return {
        "schema_version": 1,
        "mode": mode,
        "capabilities": {
            "catalogue": {
                "provider": "huggingface",
                "browse": True,
                "inspect": True,
            },
            "artifact_store": {
                "provider": provider,
                "list": native,
                "acquire": native,
                "delete": native,
            },
            "profile_service": {
                "provider": provider,
                "read": native,
                "write": native,
            },
            "runtime_controller": {
                "provider": provider,
                "status": native,
                "start": native,
                "stop": native,
                "logs": native,
            },
        },
    }


def require_cookbook_capability(group: str, action: str) -> None:
    document = cookbook_capabilities()
    capability = document["capabilities"].get(group) or {}
    if capability.get(action) is True:
        return
    name = f"{group}.{action}"
    raise HTTPException(
        status_code=501,
        detail=(
            f"Cookbook capability {name} is unavailable in external mode. "
            "Configure a provider or set OUTIS_COOKBOOK_MODE=native to use "
            "the legacy Odysseus host-side implementation."
        ),
    )
