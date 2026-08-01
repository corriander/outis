"""HTTP client for the external ArtifactStore inventory contract."""

from __future__ import annotations

import json

import httpx

from artifact_store.config import (
    ArtifactStoreConfiguration,
    ArtifactStoreConfigurationError,
    DEFAULT_PROVIDER_NAME,
    persisted_configuration_present,
    resolve_artifact_store_configuration,
    validated_base_url,
)


# The inventory document is small in practice. This ceiling still refuses a
# hostile or misconfigured provider that streams an unbounded body. It is
# enforced incrementally while the body streams in, so an oversize or
# never-ending response is abandoned rather than fully buffered first.
MAX_INVENTORY_BYTES = 5 * 1024 * 1024


class ArtifactStoreError(RuntimeError):
    """The configured provider returned an invalid inventory response."""


class ArtifactStoreUnavailable(ArtifactStoreError):
    """The configured provider could not be reached."""


def configured_artifact_store_name() -> str:
    try:
        configuration = resolve_artifact_store_configuration()
    except ArtifactStoreConfigurationError:
        return DEFAULT_PROVIDER_NAME
    return configuration.name if configuration else DEFAULT_PROVIDER_NAME


def artifact_store_configured() -> bool:
    try:
        return resolve_artifact_store_configuration() is not None
    except ArtifactStoreConfigurationError:
        # A present-but-invalid persisted file is still an intended external
        # provider. Advertise the operation so its route reports a 502 config
        # fault rather than pretending the capability was never configured.
        return persisted_configuration_present()


class ArtifactStoreClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        try:
            self.base_url = validated_base_url(base_url)
        except ArtifactStoreConfigurationError as exc:
            raise ArtifactStoreError(str(exc)) from exc
        self.token = token.strip() if token else None
        self.timeout_seconds = timeout_seconds
        # Test seam only: production leaves this None so httpx builds its own
        # transport. It never alters follow_redirects / trust_env, which stay
        # off regardless of the transport supplied.
        self._transport = transport

    @classmethod
    def from_configuration(
        cls,
        configuration: ArtifactStoreConfiguration | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "ArtifactStoreClient | None":
        if configuration is None:
            return None
        return cls(
            configuration.base_url,
            token=configuration.token,
            timeout_seconds=configuration.timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_config(cls) -> "ArtifactStoreClient | None":
        try:
            return cls.from_configuration(resolve_artifact_store_configuration())
        except ArtifactStoreConfigurationError as exc:
            raise ArtifactStoreError(str(exc)) from exc

    @classmethod
    def from_env(cls) -> "ArtifactStoreClient | None":
        """Backward-compatible name for the unified configuration resolver."""
        return cls.from_config()

    async def list_artifacts(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "GET", f"{self.base_url}/v1/artifacts", headers=headers
                ) as response:
                    if response.status_code != 200:
                        raise ArtifactStoreUnavailable(
                            f"ArtifactStore provider returned HTTP {response.status_code}"
                        )
                    content = await self._read_capped(response)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ArtifactStoreUnavailable("ArtifactStore provider is unreachable") from exc
        except httpx.HTTPError as exc:
            raise ArtifactStoreUnavailable("ArtifactStore provider request failed") from exc

        try:
            document = json.loads(content)
        except ValueError as exc:
            raise ArtifactStoreError("ArtifactStore provider returned invalid JSON") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ArtifactStoreError("ArtifactStore provider returned an unsupported schema")
        if not isinstance(document.get("provider"), dict):
            raise ArtifactStoreError("ArtifactStore provider identity is missing")
        if not isinstance(document.get("artifacts"), list):
            raise ArtifactStoreError("ArtifactStore artifacts must be a list")
        return document

    async def _read_capped(self, response: httpx.Response) -> bytes:
        """Read the response body, enforcing the byte cap while streaming.

        A trustworthy declared ``Content-Length`` is rejected before any body is
        read; the running total is still checked on every chunk so a chunked or
        length-lying response cannot exceed the cap either.
        """
        limit = MAX_INVENTORY_BYTES
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    raise ArtifactStoreError(
                        "ArtifactStore inventory exceeds the 5 MiB response limit"
                    )
            except ValueError:
                pass  # untrustworthy header; the streaming check still applies
        total = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise ArtifactStoreError(
                    "ArtifactStore inventory exceeds the 5 MiB response limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)
