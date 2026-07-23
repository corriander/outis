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

from artifact_store.client import artifact_store_configured, configured_artifact_store_name
from profile_service.client import (
    configured_profile_service_name,
    profile_service_configured,
)


_NATIVE_VALUES = {"native", "odysseus"}


def cookbook_capabilities() -> dict:
    raw_mode = os.getenv("OUTIS_COOKBOOK_MODE", "native").strip().lower()
    native = raw_mode in _NATIVE_VALUES
    mode = "native" if native else "external"
    provider = "odysseus-native" if native else None
    external_artifacts = artifact_store_configured()
    artifact_list_provider = configured_artifact_store_name() if external_artifacts else provider
    # Inventory and profile providers are selected independently. An external
    # ProfileService is an additive adapter: it never re-enables the inherited
    # local profile routes (which stay gated on the native ``read``/``write``
    # flags below), and it is only reachable through the same-origin proxy.
    external_profiles = profile_service_configured()
    profile_provider = configured_profile_service_name() if external_profiles else provider

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
                "provider": artifact_list_provider,
                "list": external_artifacts or native,
                "acquire": native,
                "delete": native,
                "operation_providers": {
                    "list": artifact_list_provider,
                    "acquire": provider,
                    "delete": provider,
                },
            },
            "profile_service": {
                "provider": profile_provider,
                # ``read``/``write`` gate the inherited host-side profile routes
                # and remain native-only; an external provider must not switch
                # them on, or the proxy would fall through to local files.
                "read": native,
                "write": native,
                # A conforming external ProfileService is configured. Its
                # read/write is reached solely through the same-origin proxy at
                # ``/api/cookbook/profile-service``. Accepted artifact
                # authorities are advertised by that service's discovery
                # document, not inferred here: the synchronous capability
                # document cannot honestly enumerate them without live I/O, so
                # the browser reads them from the proxied discovery response.
                "external": external_profiles,
                "operation_providers": {
                    "read": profile_provider if external_profiles else provider,
                    "write": profile_provider if external_profiles else provider,
                },
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
