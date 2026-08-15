"""Admin-gated same-origin proxy for external profile-set application."""

import json

from core.middleware import require_admin
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from runtime_controller.client import (
    RuntimeControllerClient,
    RuntimeControllerError,
    RuntimeControllerInvalid,
    RuntimeControllerUnauthorized,
    RuntimeControllerUnavailable,
)

_PREFIX = "/api/cookbook/runtime-controller"
MAX_RUNTIME_CONTROLLER_REQUEST_BYTES = 1024


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "errors": [{"pointer": "/", "code": code, "message": message}],
            "warnings": [],
        },
    )


def setup_runtime_controller_routes() -> APIRouter:
    router = APIRouter()

    def _client() -> RuntimeControllerClient | JSONResponse:
        try:
            client = RuntimeControllerClient.from_config()
        except RuntimeControllerError:
            return _envelope(
                502,
                "runtime_controller_invalid",
                "The configured RuntimeController is misconfigured.",
            )
        if client is None:
            return _envelope(
                501,
                "runtime_controller_not_configured",
                "No external RuntimeController provider is configured.",
            )
        return client

    @router.post(f"{_PREFIX}/apply")
    async def apply_profiles(request: Request) -> Response:
        require_admin(request)
        declared = request.headers.get("Content-Length")
        try:
            if declared is not None and int(declared) > MAX_RUNTIME_CONTROLLER_REQUEST_BYTES:
                return _envelope(413, "request_too_large", "Request body is too large.")
        except ValueError:
            pass
        total = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_RUNTIME_CONTROLLER_REQUEST_BYTES:
                return _envelope(413, "request_too_large", "Request body is too large.")
            chunks.append(chunk)
        try:
            body = json.loads(b"".join(chunks) or b"{}")
        except ValueError:
            return _envelope(400, "invalid_json", "Request body is not valid JSON.")
        if not isinstance(body, dict) or set(body) - {"allow_eviction"}:
            return _envelope(400, "invalid_request_body", "Only allow_eviction may be supplied.")
        allow_eviction = body.get("allow_eviction", False)
        if not isinstance(allow_eviction, bool):
            return _envelope(400, "invalid_request_body", "allow_eviction must be a boolean.")
        client = _client()
        if isinstance(client, JSONResponse):
            return client
        try:
            response = await client.apply_profiles(allow_eviction=allow_eviction)
        except RuntimeControllerUnauthorized:
            return _envelope(
                502,
                "runtime_controller_unauthorized",
                "The configured RuntimeController rejected the server-side token.",
            )
        except RuntimeControllerUnavailable:
            return _envelope(
                502,
                "runtime_controller_unreachable",
                "The configured RuntimeController could not be reached.",
            )
        except RuntimeControllerInvalid:
            return _envelope(
                502,
                "runtime_controller_invalid",
                "The configured RuntimeController returned an invalid response.",
            )
        if response.status_code != 200:
            # Conflict metadata (especially the loaded model names and retry
            # instruction) is the browser's decision input and stays intact.
            return JSONResponse(status_code=response.status_code, content=response.body)
        data = response.body["data"]
        # The upstream result contains controller diagnostics and its resolved
        # topology owner. The Cookbook needs only state and pickup evidence;
        # keeping target/device/report server-side prevents the UI from growing
        # a dependency on provider nouns or topology.
        public_body = {
            "data": {
                key: data[key] for key in ("state", "configuration", "verification", "evicted")
            },
            "warnings": response.body.get("warnings", []),
        }
        return JSONResponse(status_code=200, content=public_body)

    return router
