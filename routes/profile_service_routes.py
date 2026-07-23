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
from urllib.parse import urlsplit

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
)

_PREFIX = "/api/cookbook/profile-service"
# Upstream profile resources live at ``/v1/profiles/{id}``; the browser must
# reach them through this proxy prefix instead.
_UPSTREAM_PROFILE_PREFIX = "/v1/profiles/"


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    """A structured error envelope in the ProfileService v1 shape."""
    return JSONResponse(
        status_code=status_code,
        content={"errors": [{"pointer": "/", "code": code, "message": message}], "warnings": []},
    )


def _translate_location(location: str | None) -> str | None:
    """Map an upstream profile ``Location`` onto this proxy, or drop it.

    The upstream returns a relative resource location such as
    ``/v1/profiles/example``. Rewritten, the browser can follow it back through
    the proxy. Anything else -- an absolute or off-origin URL, a decorated URL,
    or a path that is not a single-segment profile resource -- is suppressed
    rather than forwarded, so the browser is never sent to a route that does not
    exist here or to another origin.
    """
    if not location:
        return None
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = parsed.path
    if not path.startswith(_UPSTREAM_PROFILE_PREFIX):
        return None
    remainder = path[len(_UPSTREAM_PROFILE_PREFIX):]
    if not remainder or "/" in remainder:
        return None
    return f"{_PREFIX}/profiles/{remainder}"


def _forward(response: ProfileServiceResponse) -> Response:
    headers: dict[str, str] = {}
    if response.etag:
        headers["ETag"] = response.etag
    location = _translate_location(response.location)
    if location:
        headers["Location"] = location
    if response.body is None:
        return Response(status_code=response.status_code, headers=headers)
    return JSONResponse(status_code=response.status_code, content=response.body, headers=headers)


def setup_profile_service_routes() -> APIRouter:
    router = APIRouter()

    def _client() -> ProfileServiceClient | JSONResponse:
        try:
            client = ProfileServiceClient.from_env()
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

    async def _json_body(request: Request) -> Any:
        raw = await request.body()
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
            client.replace_profile(profile_id, body.get("values"), if_match=_if_match(request))
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
