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

import json
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from profile_service.config import (
    DEFAULT_PROVIDER_NAME,
    ProfileServiceConfiguration,
    ProfileServiceConfigurationError,
    persisted_configuration_present,
    resolve_profile_service_configuration,
    validated_base_url,
)


# Discovery, form, and profile documents are small. A generous ceiling still
# refuses a hostile or misconfigured service that streams an unbounded body. The
# limit is enforced incrementally while the body streams in, so an oversize or
# never-ending response is abandoned rather than fully buffered first.
MAX_PROFILE_SERVICE_BYTES = 5 * 1024 * 1024

# Only these keys ever leave Outis inside an ArtifactRef. A profile is addressed
# by provider-scoped identity ({authority, artifact_id} plus an optional
# observation token); the provider resolves the launch path itself. Any other
# key a caller attaches -- notably a concrete model path -- is dropped here, so a
# filesystem path can never be submitted through Outis.
_ARTIFACT_REF_KEYS = ("authority", "artifact_id", "observation")


class ProfileServiceError(RuntimeError):
    """Base error: the configured ProfileService cannot be used as configured."""


class ProfileServiceRequestError(ProfileServiceError):
    """The *caller* supplied something this client cannot submit.

    Distinct from every other error here, which describes a fault in the
    configured service or its response. A caller fault is the browser's, so
    the proxy answers 4xx rather than blaming the provider with a 502.

    Subclasses ``ProfileServiceError`` so an existing handler that catches the
    base still catches this; handlers that care must catch it first.
    """


class ProfileServiceUnavailable(ProfileServiceError):
    """The configured ProfileService could not be reached."""


class ProfileServiceUnauthorized(ProfileServiceError):
    """The ProfileService rejected the server-side bearer token (upstream 401)."""


class ProfileServiceInvalid(ProfileServiceError):
    """The ProfileService returned a response that violates the v1 contract."""


# -- wire models ----------------------------------------------------------
#
# These validate the *structure* of an upstream response without flattening it:
# a validated body is checked, then the original dict is forwarded unchanged so
# unknown additive fields, exact ordering, and provider-defined values survive.
# ``extra="allow"`` is set everywhere the v1 contract permits additive fields.


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class _WireError(_WireModel):
    pointer: str
    code: str
    message: str


class _WireWarning(_WireModel):
    code: str
    message: str
    pointer: str | None = None


class _ErrorEnvelope(_WireModel):
    """Every 4xx/5xx response carries this shape (an unstructured error is not
    a valid v1 response and becomes ``ProfileServiceInvalid``)."""

    errors: list[_WireError] = Field(min_length=1)
    warnings: list[_WireWarning] = Field(default_factory=list)


class _ServiceDoc(_WireModel):
    contract_version: int
    accepted_authorities: list[Any]


class _FormDoc(_WireModel):
    fields: list[Any]


class _ProfileObj(_WireModel):
    id: str
    values: dict[str, Any]


class _ProfileData(_WireModel):
    profile: _ProfileObj


class _ProfileEnvelope(_WireModel):
    data: _ProfileData
    warnings: list[_WireWarning] = Field(default_factory=list)


class _ListData(_WireModel):
    # Each entry is a full profile object, not an untyped blob: a malformed
    # summary (missing id/values) fails validation rather than being forwarded.
    profiles: list[_ProfileObj]


class _ListEnvelope(_WireModel):
    data: _ListData
    warnings: list[_WireWarning] = Field(default_factory=list)


class _DraftData(_WireModel):
    # A v1 draft echoes the form version and the artifact_ref it is bound to
    # alongside the seeded values, so the editor knows what it is drafting for.
    values: dict[str, Any]
    form_version: int
    artifact_ref: dict[str, Any]


class _DraftEnvelope(_WireModel):
    data: _DraftData
    warnings: list[_WireWarning] = Field(default_factory=list)


class _PreviewData(_WireModel):
    values: dict[str, Any]


class _PreviewEnvelope(_WireModel):
    # Preview answers 200 with either ``data: {values}`` (accepted) or
    # ``data: null`` plus a non-empty ``errors`` list (rejected). An empty
    # object carries neither and is not a valid v1 preview response.
    data: _PreviewData | None = None
    errors: list[_WireError] = Field(default_factory=list)
    warnings: list[_WireWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_data_or_errors(self) -> "_PreviewEnvelope":
        if self.data is None and not self.errors:
            raise ValueError("preview response must carry either data or errors")
        return self


def _validate(body: Any, model: type[BaseModel], what: str) -> None:
    try:
        model.model_validate(body)
    except ValidationError as exc:
        raise ProfileServiceInvalid(
            f"ProfileService returned a malformed {what} response"
        ) from exc


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
    try:
        configuration = resolve_profile_service_configuration()
    except ProfileServiceConfigurationError:
        return DEFAULT_PROVIDER_NAME
    return configuration.name if configuration else DEFAULT_PROVIDER_NAME


def profile_service_configured() -> bool:
    try:
        return resolve_profile_service_configuration() is not None
    except ProfileServiceConfigurationError:
        # A present-but-invalid persisted file is still an intended external
        # provider. Advertise the capability so its proxy reports a 502 config
        # fault rather than pretending the service was never configured.
        return persisted_configuration_present()


def normalise_artifact_ref(ref: Any) -> dict:
    """Project a caller-supplied ArtifactRef onto the three wire keys.

    A concrete model path (or any other extra key) is dropped rather than
    forwarded: Outis submits provider-scoped identity, never a path variant it
    would have to select or translate.
    """
    if not isinstance(ref, Mapping):
        raise ProfileServiceRequestError("artifact_ref must be an object")
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
        try:
            self.base_url = validated_base_url(base_url)
        except ProfileServiceConfigurationError as exc:
            raise ProfileServiceError(str(exc)) from exc
        self.token = token.strip() if token else None
        self.timeout_seconds = timeout_seconds
        self.name = (name.strip() if name else None) or configured_profile_service_name()
        # Test seam only: production leaves this None so httpx builds its own
        # transport. It never alters follow_redirects / trust_env, which stay
        # off regardless of the transport supplied.
        self._transport = transport

    @classmethod
    def from_configuration(
        cls,
        configuration: ProfileServiceConfiguration | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "ProfileServiceClient | None":
        if configuration is None:
            return None
        return cls(
            configuration.base_url,
            token=configuration.token,
            timeout_seconds=configuration.timeout_seconds,
            name=configuration.name,
            transport=transport,
        )

    @classmethod
    def from_config(cls) -> "ProfileServiceClient | None":
        try:
            return cls.from_configuration(resolve_profile_service_configuration())
        except ProfileServiceConfigurationError as exc:
            raise ProfileServiceError(str(exc)) from exc

    @classmethod
    def from_env(cls) -> "ProfileServiceClient | None":
        """Backward-compatible name for the unified configuration resolver."""
        return cls.from_config()

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
                async with client.stream(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                ) as response:
                    if response.status_code == 401:
                        raise ProfileServiceUnauthorized(
                            "The configured ProfileService rejected the server-side token"
                        )
                    if 300 <= response.status_code < 400:
                        # follow_redirects is off by design; a v1 endpoint never
                        # redirects, so a 3xx is an invalid contract response.
                        raise ProfileServiceInvalid(
                            "ProfileService returned an unexpected redirect"
                        )
                    content = await self._read_capped(response)
                    return self._build_response(
                        response.status_code, content, response.headers
                    )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProfileServiceUnavailable("ProfileService provider is unreachable") from exc
        except httpx.HTTPError as exc:
            raise ProfileServiceUnavailable("ProfileService provider request failed") from exc

    async def _read_capped(self, response: httpx.Response) -> bytes:
        """Read the response body, enforcing the byte cap while streaming.

        A trustworthy declared ``Content-Length`` is rejected before any body is
        read; the running total is still checked on every chunk so a chunked or
        length-lying response cannot exceed the cap either.
        """
        limit = MAX_PROFILE_SERVICE_BYTES
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    raise ProfileServiceInvalid(
                        "ProfileService response exceeds the size limit"
                    )
            except ValueError:
                pass  # untrustworthy header; the streaming check still applies
        total = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise ProfileServiceInvalid(
                    "ProfileService response exceeds the size limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _build_response(
        self, status_code: int, content: bytes, headers: httpx.Headers
    ) -> ProfileServiceResponse:
        etag = headers.get("ETag")
        location = headers.get("Location")
        if status_code == 204 or not content:
            return ProfileServiceResponse(status_code, None, etag, location)
        try:
            body = json.loads(content)
        except ValueError as exc:
            raise ProfileServiceInvalid("ProfileService returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProfileServiceInvalid("ProfileService returned a non-object body")
        return ProfileServiceResponse(status_code, body, etag, location)

    def _validated(
        self,
        response: ProfileServiceResponse,
        success_model: type[BaseModel] | None,
        success_status: int,
    ) -> ProfileServiceResponse:
        """Validate an upstream envelope's structure, then forward it unchanged.

        Success is endpoint-specific: each operation defines the exact 2xx it
        may answer with -- 201 for create, 204 for delete, 200 for the rest.
        Any other 2xx (a delete replying 200-with-body, a create replying 200)
        violates the contract and becomes ``ProfileServiceInvalid`` rather than
        being waved through. On the defined success the body is checked against
        the endpoint's model; a bodyless success is valid only where the model
        is ``None`` (DELETE's 204). Any non-2xx must carry the structured error
        envelope. Either way the original dict is returned so errors, warnings,
        and additive fields are preserved.
        """
        if 200 <= response.status_code < 300:
            if response.status_code != success_status:
                raise ProfileServiceInvalid(
                    f"ProfileService answered {response.status_code} where "
                    f"{success_status} is the contract success status"
                )
            if success_model is None:
                return response
            _validate(response.body if response.body is not None else {},
                      success_model, "success")
        else:
            _validate(response.body if response.body is not None else {},
                      _ErrorEnvelope, "error")
        return response

    # -- discovery ---------------------------------------------------------

    async def get_service(self) -> ProfileServiceResponse:
        response = await self._request("GET", "/v1/service")
        if response.status_code != 200 or response.body is None:
            raise ProfileServiceInvalid("ProfileService discovery returned no document")
        _validate(response.body, _ServiceDoc, "discovery")
        if response.body.get("contract_version") != 1:
            raise ProfileServiceInvalid(
                "ProfileService advertises an unsupported contract version"
            )
        return response

    async def get_form(self) -> ProfileServiceResponse:
        response = await self._request("GET", "/v1/form")
        if response.status_code != 200 or response.body is None:
            raise ProfileServiceInvalid("ProfileService form returned no document")
        _validate(response.body, _FormDoc, "form")
        return response

    # -- stateless draft / preview ----------------------------------------

    async def create_draft(self, artifact_ref: Any) -> ProfileServiceResponse:
        response = await self._request(
            "POST",
            "/v1/profiles/draft",
            json_body={"artifact_ref": normalise_artifact_ref(artifact_ref), "template": None},
        )
        return self._validated(response, _DraftEnvelope, 200)

    async def preview(self, values: Any) -> ProfileServiceResponse:
        response = await self._request(
            "POST", "/v1/profiles/preview", json_body={"values": values}
        )
        return self._validated(response, _PreviewEnvelope, 200)

    # -- profile CRUD ------------------------------------------------------

    async def list_profiles(self) -> ProfileServiceResponse:
        response = await self._request("GET", "/v1/profiles")
        return self._validated(response, _ListEnvelope, 200)

    async def read_profile(self, profile_id: str) -> ProfileServiceResponse:
        response = await self._request("GET", f"/v1/profiles/{_encode_id(profile_id)}")
        return self._validated(response, _ProfileEnvelope, 200)

    async def create_profile(self, artifact_ref: Any, values: Any) -> ProfileServiceResponse:
        response = await self._request(
            "POST",
            "/v1/profiles",
            json_body={"artifact_ref": normalise_artifact_ref(artifact_ref), "values": values},
        )
        return self._validated(response, _ProfileEnvelope, 201)

    async def replace_profile(
        self, profile_id: str, values: Any, *, if_match: str | None
    ) -> ProfileServiceResponse:
        response = await self._request(
            "PUT",
            f"/v1/profiles/{_encode_id(profile_id)}",
            json_body={"values": values},
            if_match=if_match,
        )
        return self._validated(response, _ProfileEnvelope, 200)

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
        response = await self._request(
            "PATCH",
            f"/v1/profiles/{_encode_id(profile_id)}",
            json_body=payload,
            if_match=if_match,
        )
        return self._validated(response, _ProfileEnvelope, 200)

    async def delete_profile(
        self, profile_id: str, *, if_match: str | None
    ) -> ProfileServiceResponse:
        response = await self._request(
            "DELETE", f"/v1/profiles/{_encode_id(profile_id)}", if_match=if_match
        )
        # Success is a bodyless 204; a 2xx that is not 204, or any other status
        # without an error envelope, is rejected as an invalid response.
        return self._validated(response, None, 204)


def _encode_id(profile_id: str) -> str:
    from urllib.parse import quote

    return quote(str(profile_id), safe="")
