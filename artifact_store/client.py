"""HTTP client for the external ArtifactStore inventory contract."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import httpx


MAX_INVENTORY_BYTES = 5 * 1024 * 1024


class ArtifactStoreError(RuntimeError):
    """The configured provider returned an invalid inventory response."""


class ArtifactStoreUnavailable(ArtifactStoreError):
    """The configured provider could not be reached."""


def configured_artifact_store_name() -> str:
    return os.getenv("OUTIS_ARTIFACT_STORE_NAME", "external-artifact-store").strip() or "external-artifact-store"


def artifact_store_configured() -> bool:
    return bool(os.getenv("OUTIS_ARTIFACT_STORE_URL", "").strip())


def _configured_timeout() -> float:
    try:
        value = float(os.getenv("OUTIS_ARTIFACT_STORE_TIMEOUT", "10") or "10")
    except ValueError:
        return 10.0
    return max(0.5, min(value, 60.0))


def _validated_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ArtifactStoreError("ArtifactStore URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ArtifactStoreError("ArtifactStore URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ArtifactStoreError("ArtifactStore URL must not contain credentials, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class ArtifactStoreClient:
    def __init__(self, base_url: str, token: str | None = None, timeout_seconds: float = 10.0):
        self.base_url = _validated_base_url(base_url)
        self.token = token.strip() if token else None
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "ArtifactStoreClient | None":
        base_url = os.getenv("OUTIS_ARTIFACT_STORE_URL", "").strip()
        if not base_url:
            return None
        return cls(
            base_url,
            token=os.getenv("OUTIS_ARTIFACT_STORE_TOKEN", "").strip() or None,
            timeout_seconds=_configured_timeout(),
        )

    async def list_artifacts(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(f"{self.base_url}/v1/artifacts", headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ArtifactStoreUnavailable("ArtifactStore provider is unreachable") from exc
        except httpx.HTTPError as exc:
            raise ArtifactStoreUnavailable("ArtifactStore provider request failed") from exc

        if response.status_code != 200:
            raise ArtifactStoreUnavailable(f"ArtifactStore provider returned HTTP {response.status_code}")
        if len(response.content) > MAX_INVENTORY_BYTES:
            raise ArtifactStoreError("ArtifactStore inventory exceeds the 5 MiB response limit")
        try:
            document = response.json()
        except ValueError as exc:
            raise ArtifactStoreError("ArtifactStore provider returned invalid JSON") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ArtifactStoreError("ArtifactStore provider returned an unsupported schema")
        if not isinstance(document.get("provider"), dict):
            raise ArtifactStoreError("ArtifactStore provider identity is missing")
        if not isinstance(document.get("artifacts"), list):
            raise ArtifactStoreError("ArtifactStore artifacts must be a list")
        return document
