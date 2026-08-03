"""Same-origin proxy for an external ProfileService v1 provider.

Every route sits behind the existing Outis admin/session authority and forwards
to the configured external service through ``ProfileServiceClient``. The service
bearer token is held server-side only: it is never placed in a browser response
body, header, cookie, or local storage. Structured provider errors and warnings
are preserved verbatim so a future profile editor can render field- and
profile-level feedback; the one translation is an upstream 401, which becomes a
502 server-configuration error rather than a browser 401 (the browser is
authorised; the server is not).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from core.middleware import require_admin
from profile_service.client import (
    ProfileServiceClient,
    ProfileServiceInvalid,
    ProfileServiceResponse,
    ProfileServiceUnauthorized,
    ProfileServiceUnavailable,
    ProfileServiceError,
    ProfileServiceRequestError,
)

_PREFIX = "/api/cookbook/profile-service"

# Mirrors MAX_PROFILE_SERVICE_BYTES on the response side. Profile documents are
# small; this ceiling refuses a browser that streams an unbounded body into the
# proxy. The route is admin-gated, so this is depth rather than a perimeter.
MAX_PROFILE_SERVICE_REQUEST_BYTES = 5 * 1024 * 1024


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    """A structured error envelope in the ProfileService v1 shape."""
    return JSONResponse(
        status_code=status_code,
        content={"errors": [{"pointer": "/", "code": code, "message": message}], "warnings": []},
    )


def _proxy_location(profile_id: str) -> str | None:
    """Map a profile id onto exactly one safe proxy path segment, or drop it.

    The id is a single segment: reject anything empty, a bare ``.``/``..``, or a
    value that carries a path separator -- directly or once percent-decoded, so
    ``%2e%2e`` and ``%2Fadmin`` cannot smuggle a second segment or a traversal.
    The surviving id is then percent-encoded canonically, never interpolated raw.
    """
    if not profile_id or profile_id in (".", ".."):
        return None
    if "/" in profile_id or "\\" in profile_id:
        return None
    decoded = unquote(profile_id)
    if decoded in (".", "..") or "/" in decoded or "\\" in decoded:
        return None
    return f"{_PREFIX}/profiles/{quote(profile_id, safe='')}"


def _location_from_body(body: Any) -> str | None:
    """Derive the created profile's proxy Location from the validated body id.

    The upstream ``Location`` header is not trusted for its value; the profile
    id has already passed structural validation in the client, so the proxy
    Location is built from that id (canonically re-encoded) instead.
    """
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    profile = data.get("profile") if isinstance(data, dict) else None
    profile_id = profile.get("id") if isinstance(profile, dict) else None
    if not isinstance(profile_id, str):
        return None
    return _proxy_location(profile_id)


def _forward(response: ProfileServiceResponse) -> Response:
    headers: dict[str, str] = {}
    if response.etag:
        headers["ETag"] = response.etag
    # Emit a Location only when the upstream offered one (a create), but take its
    # value from the validated body id -- never from the untrusted header path.
    if response.location:
        location = _location_from_body(response.body)
        if location:
            headers["Location"] = location
    if response.body is None:
        return Response(status_code=response.status_code, headers=headers)
    return JSONResponse(status_code=response.status_code, content=response.body, headers=headers)


def setup_profile_service_routes() -> APIRouter:
    router = APIRouter()

    def _client() -> ProfileServiceClient | JSONResponse:
        try:
            client = ProfileServiceClient.from_config()
        except ProfileServiceError:
            # A misconfigured URL is a server-side fault, not a browser one.
            return _envelope(
                502,
                "profile_service_invalid",
                "The configured ProfileService is misconfigured.",
            )
        if client is None:
            return _envelope(
                501,
                "profile_service_not_configured",
                "No external ProfileService provider is configured.",
            )
        return client

    async def _run(coro) -> Response:
        """Await an upstream call, mapping transport/auth/contract faults.

        A domain outcome (2xx or a structured 4xx/5xx) is forwarded unchanged.
        """
        try:
            return _forward(await coro)
        except ProfileServiceRequestError as exc:
            # The browser sent something unusable. Caught before the base class
            # below, which would otherwise blame the provider for a 502.
            return _envelope(400, "invalid_request_body", str(exc))
        except ProfileServiceUnauthorized:
            return _envelope(
                502,
                "profile_service_unauthorized",
                "The configured ProfileService rejected the server-side token.",
            )
        except ProfileServiceUnavailable:
            return _envelope(
                502,
                "profile_service_unreachable",
                "The configured ProfileService could not be reached.",
            )
        except ProfileServiceInvalid:
            return _envelope(
                502,
                "profile_service_invalid",
                "The configured ProfileService returned an invalid response.",
            )
        except ProfileServiceError:
            return _envelope(
                502,
                "profile_service_invalid",
                "The configured ProfileService is misconfigured.",
            )

    async def _read_capped_request(request: Request) -> bytes | JSONResponse:
        """Read the browser's body, enforcing the byte cap while streaming.

        Mirrors the client's response-side cap. Without this the proxy buffers
        an unbounded body before anything inspects it, and neither Outis nor
        the upstream service imposes a request limit of its own. A trustworthy
        declared Content-Length is refused before any body is read; the running
        total is still checked per chunk so a chunked or length-lying request
        cannot exceed the cap either.
        """
        too_large = _envelope(
            413,
            "request_too_large",
            f"Request body exceeds the {MAX_PROFILE_SERVICE_REQUEST_BYTES} byte limit.",
        )
        declared = request.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAX_PROFILE_SERVICE_REQUEST_BYTES:
                    return too_large
            except ValueError:
                pass  # untrustworthy header; the streaming check still applies
        total = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_PROFILE_SERVICE_REQUEST_BYTES:
                return too_large
            chunks.append(chunk)
        return b"".join(chunks)

    async def _json_body(request: Request) -> Any:
        raw = await _read_capped_request(request)
        if isinstance(raw, JSONResponse):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return _envelope(400, "invalid_json", "Request body is not valid JSON.")
        if not isinstance(parsed, dict):
            # A bare array, string, number, or null is valid JSON but not a
            # request object; the routes below index it as one.
            return _envelope(400, "invalid_request_body", "Request body must be a JSON object.")
        return parsed

    def _if_match(request: Request) -> str | None:
        # Forward the browser's explicit expected-version only. Never synthesise
        # a wildcard overwrite: a missing header means the provider answers 428.
        return request.headers.get("If-Match")

    @router.get(_PREFIX)
    async def service_document(request: Request):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        try:
            response = await client.get_service()
        except ProfileServiceUnauthorized:
            return _envelope(
                502,
                "profile_service_unauthorized",
                "The configured ProfileService rejected the server-side token.",
            )
        except ProfileServiceUnavailable:
            return _envelope(
                502,
                "profile_service_unreachable",
                "The configured ProfileService could not be reached.",
            )
        except ProfileServiceError:
            return _envelope(
                502,
                "profile_service_invalid",
                "The configured ProfileService returned an invalid discovery document.",
            )
        # Add the operator-facing display name for the browser. The token never
        # travels; only this label and the upstream document cross.
        body = dict(response.body or {})
        body["provider_name"] = client.name
        return JSONResponse(status_code=200, content=body)

    @router.get(f"{_PREFIX}/form")
    async def form_document(request: Request):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        return await _run(client.get_form())

    @router.post(f"{_PREFIX}/draft")
    async def create_draft(request: Request):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await _run(client.create_draft(body.get("artifact_ref")))

    @router.post(f"{_PREFIX}/preview")
    async def preview(request: Request):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await _run(client.preview(body.get("values")))

    @router.get(f"{_PREFIX}/profiles")
    async def list_profiles(request: Request):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        return await _run(client.list_profiles())

    @router.post(f"{_PREFIX}/profiles")
    async def create_profile(request: Request):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await _run(client.create_profile(body.get("artifact_ref"), body.get("values")))

    @router.get(f"{_PREFIX}/profiles/{{profile_id}}")
    async def read_profile(request: Request, profile_id: str):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        return await _run(client.read_profile(profile_id))

    @router.put(f"{_PREFIX}/profiles/{{profile_id}}")
    async def replace_profile(request: Request, profile_id: str):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await _run(
            client.replace_profile(
                profile_id,
                body.get("values"),
                if_match=_if_match(request),
                artifact_ref=body.get("artifact_ref"),
                rebind=bool(body.get("rebind")),
            )
        )

    @router.patch(f"{_PREFIX}/profiles/{{profile_id}}")
    async def patch_profile(request: Request, profile_id: str):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await _run(
            client.patch_profile(
                profile_id,
                set_values=body.get("set"),
                clear=body.get("clear"),
                if_match=_if_match(request),
            )
        )

    @router.delete(f"{_PREFIX}/profiles/{{profile_id}}")
    async def delete_profile(request: Request, profile_id: str):
        require_admin(request)
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        return await _run(client.delete_profile(profile_id, if_match=_if_match(request)))

    return router
