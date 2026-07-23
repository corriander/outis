"""HTTP client for the external ProfileService v1 contract.

Mirrors ``artifact_store/client.py``: a configured base URL, a server-side
bearer token that never crosses to the browser, ``follow_redirects=False`` and
``trust_env=False`` so an attacker cannot bounce the server through an ambient
proxy or a ``Location`` redirect, a clamped timeout, and a bounded response
size.

The client distinguishes four failure classes so the proxy can react
correctly without flattening the provider's structured field/profile errors:

- ``ProfileServiceUnavailable`` -- the service could not be reached (network,
  timeout, or an unexpected redirect that we refuse to follow);
- ``ProfileServiceUnauthorized`` -- the service rejected the server-side bearer
  token (upstream 401); the browser is authorised, so this is a server
  misconfiguration, surfaced as 502 not 401;
- ``ProfileServiceInvalid`` -- the response violates the minimum v1 structural
  contract (bad JSON, oversize body, wrong contract version, non-object body);
- an upstream *domain* outcome (2xx with data, or a 4xx/5xx carrying the
  structured ``{errors, warnings}`` envelope) is returned as a
  ``ProfileServiceResponse`` for the proxy to forward verbatim.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


# Discovery, form, and profile documents are small. A generous ceiling still
# refuses a hostile or misconfigured service that streams an unbounded body.
MAX_PROFILE_SERVICE_BYTES = 5 * 1024 * 1024

# Only these keys ever leave Outis inside an ArtifactRef. Anything else a caller
# attaches -- notably a concrete ``model_path`` -- is dropped here: Outis submits
# an ArtifactRef and never a model path (WS-04 path boundary).
_ARTIFACT_REF_KEYS = ("authority", "artifact_id", "observation")


class ProfileServiceError(RuntimeError):
    """Base error: the configured ProfileService cannot be used as configured."""


class ProfileServiceUnavailable(ProfileServiceError):
    """The configured ProfileService could not be reached."""


class ProfileServiceUnauthorized(ProfileServiceError):
    """The ProfileService rejected the server-side bearer token (upstream 401)."""


class ProfileServiceInvalid(ProfileServiceError):
    """The ProfileService returned a response that violates the v1 contract."""


class ProfileServiceResponse:
    """A transport-validated upstream response for the proxy to forward.

    Transport, auth, and contract failures are raised before an instance is
    ever built, so ``status_code`` here is always an upstream *domain* outcome
    (a 2xx payload or a structured 4xx/5xx envelope) that the editor renders.
    """

    __slots__ = ("status_code", "body", "etag", "location")

    def __init__(
        self,
        status_code: int,
        body: dict | None,
        etag: str | None = None,
        location: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.etag = etag
        self.location = location


def configured_profile_service_name() -> str:
    return (
        os.getenv("OUTIS_PROFILE_SERVICE_NAME", "external-profile-service").strip()
        or "external-profile-service"
    )


def profile_service_configured() -> bool:
    return bool(os.getenv("OUTIS_PROFILE_SERVICE_URL", "").strip())


def _configured_timeout() -> float:
    try:
        value = float(os.getenv("OUTIS_PROFILE_SERVICE_TIMEOUT", "10") or "10")
    except ValueError:
        return 10.0
    return max(0.5, min(value, 60.0))


def _validated_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ProfileServiceError("ProfileService URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProfileServiceError("ProfileService URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProfileServiceError(
            "ProfileService URL must not contain credentials, query, or fragment"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalise_artifact_ref(ref: Any) -> dict:
    """Project a caller-supplied ArtifactRef onto the three wire keys.

    A concrete model path (or any other extra key) is dropped rather than
    forwarded: Outis submits provider-scoped identity, never a path variant it
    would have to select or translate.
    """
    if not isinstance(ref, Mapping):
        raise ProfileServiceError("artifact_ref must be an object")
    return {key: ref[key] for key in _ARTIFACT_REF_KEYS if key in ref}


class ProfileServiceClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = _validated_base_url(base_url)
        self.token = token.strip() if token else None
        self.timeout_seconds = timeout_seconds
        self.name = (name.strip() if name else None) or configured_profile_service_name()
        # Test seam only: production leaves this None so httpx builds its own
        # transport. It never alters follow_redirects / trust_env, which stay
        # off regardless of the transport supplied.
        self._transport = transport

    @classmethod
    def from_env(cls) -> "ProfileServiceClient | None":
        base_url = os.getenv("OUTIS_PROFILE_SERVICE_URL", "").strip()
        if not base_url:
            return None
        return cls(
            base_url,
            token=os.getenv("OUTIS_PROFILE_SERVICE_TOKEN", "").strip() or None,
            timeout_seconds=_configured_timeout(),
            name=os.getenv("OUTIS_PROFILE_SERVICE_NAME", "").strip() or None,
        )

    # -- transport ---------------------------------------------------------

    def _headers(self, *, json_body: bool, if_match: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_body:
            headers["Content-Type"] = "application/json"
        if if_match is not None:
            headers["If-Match"] = if_match
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        if_match: str | None = None,
    ) -> ProfileServiceResponse:
        headers = self._headers(json_body=json_body is not None, if_match=if_match)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProfileServiceUnavailable("ProfileService provider is unreachable") from exc
        except httpx.HTTPError as exc:
            raise ProfileServiceUnavailable("ProfileService provider request failed") from exc

        if response.status_code == 401:
            raise ProfileServiceUnauthorized(
                "The configured ProfileService rejected the server-side token"
            )
        if 300 <= response.status_code < 400:
            # follow_redirects is off by design; a v1 endpoint never redirects.
            raise ProfileServiceInvalid("ProfileService returned an unexpected redirect")
        if len(response.content) > MAX_PROFILE_SERVICE_BYTES:
            raise ProfileServiceInvalid("ProfileService response exceeds the 5 MiB limit")

        etag = response.headers.get("ETag")
        location = response.headers.get("Location")
        if response.status_code == 204 or not response.content:
            return ProfileServiceResponse(response.status_code, None, etag, location)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProfileServiceInvalid("ProfileService returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProfileServiceInvalid("ProfileService returned a non-object body")
        return ProfileServiceResponse(response.status_code, body, etag, location)

    # -- discovery ---------------------------------------------------------

    async def get_service(self) -> ProfileServiceResponse:
        response = await self._request("GET", "/v1/service")
        if response.status_code != 200 or response.body is None:
            raise ProfileServiceInvalid("ProfileService discovery returned no document")
        body = response.body
        if body.get("contract_version") != 1:
            raise ProfileServiceInvalid(
                "ProfileService advertises an unsupported contract version"
            )
        if not isinstance(body.get("accepted_authorities"), list):
            raise ProfileServiceInvalid(
                "ProfileService discovery is missing accepted_authorities"
            )
        return response

    async def get_form(self) -> ProfileServiceResponse:
        response = await self._request("GET", "/v1/form")
        if response.status_code != 200 or response.body is None:
            raise ProfileServiceInvalid("ProfileService form returned no document")
        if not isinstance(response.body.get("fields"), list):
            raise ProfileServiceInvalid("ProfileService form is missing fields")
        return response

    # -- stateless draft / preview ----------------------------------------

    async def create_draft(self, artifact_ref: Any) -> ProfileServiceResponse:
        return await self._request(
            "POST",
            "/v1/profiles/draft",
            json_body={"artifact_ref": normalise_artifact_ref(artifact_ref), "template": None},
        )

    async def preview(self, values: Any) -> ProfileServiceResponse:
        return await self._request(
            "POST", "/v1/profiles/preview", json_body={"values": values}
        )

    # -- profile CRUD ------------------------------------------------------

    async def list_profiles(self) -> ProfileServiceResponse:
        return await self._request("GET", "/v1/profiles")

    async def read_profile(self, profile_id: str) -> ProfileServiceResponse:
        return await self._request("GET", f"/v1/profiles/{_encode_id(profile_id)}")

    async def create_profile(self, artifact_ref: Any, values: Any) -> ProfileServiceResponse:
        return await self._request(
            "POST",
            "/v1/profiles",
            json_body={"artifact_ref": normalise_artifact_ref(artifact_ref), "values": values},
        )

    async def replace_profile(
        self, profile_id: str, values: Any, *, if_match: str | None
    ) -> ProfileServiceResponse:
        return await self._request(
            "PUT",
            f"/v1/profiles/{_encode_id(profile_id)}",
            json_body={"values": values},
            if_match=if_match,
        )

    async def patch_profile(
        self,
        profile_id: str,
        *,
        set_values: Any | None = None,
        clear: Any | None = None,
        if_match: str | None,
    ) -> ProfileServiceResponse:
        payload: dict[str, Any] = {}
        if set_values is not None:
            payload["set"] = set_values
        if clear is not None:
            payload["clear"] = clear
        return await self._request(
            "PATCH",
            f"/v1/profiles/{_encode_id(profile_id)}",
            json_body=payload,
            if_match=if_match,
        )

    async def delete_profile(
        self, profile_id: str, *, if_match: str | None
    ) -> ProfileServiceResponse:
        return await self._request(
            "DELETE", f"/v1/profiles/{_encode_id(profile_id)}", if_match=if_match
        )


def _encode_id(profile_id: str) -> str:
    from urllib.parse import quote

    return quote(str(profile_id), safe="")
