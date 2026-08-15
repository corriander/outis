"""Same-origin RuntimeController proxy behavior."""

import json

import pytest
from fastapi import FastAPI
from runtime_controller.client import RuntimeControllerResponse
from starlette.requests import Request


def _request(body=None):
    raw = json.dumps(body if body is not None else {}).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/cookbook/runtime-controller/apply",
            "headers": [(b"content-type", b"application/json")],
            "app": FastAPI(),
        },
        receive,
    )


def _endpoint():
    from routes.runtime_controller_routes import setup_runtime_controller_routes

    return next(
        route.endpoint
        for route in setup_runtime_controller_routes().routes
        if route.path == "/api/cookbook/runtime-controller/apply"
    )


@pytest.mark.asyncio
async def test_proxy_keeps_target_server_side_and_forwards_domain_outcome(monkeypatch):
    import routes.runtime_controller_routes as routes

    calls = []

    class Client:
        async def apply_profiles(self, *, allow_eviction):
            calls.append(allow_eviction)
            return RuntimeControllerResponse(
                409,
                {
                    "errors": [
                        {
                            "pointer": "/",
                            "code": "would_evict",
                            "message": "would evict",
                            "meta": {"models": ["loaded-model"]},
                        }
                    ],
                    "warnings": [],
                },
            )

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(routes.RuntimeControllerClient, "from_config", lambda: Client())

    response = await _endpoint()(_request({"allow_eviction": False}))

    assert calls == [False]
    assert response.status_code == 409
    assert json.loads(response.body)["errors"][0]["code"] == "would_evict"


@pytest.mark.asyncio
async def test_proxy_projects_success_without_provider_topology(monkeypatch):
    import routes.runtime_controller_routes as routes

    class Client:
        async def apply_profiles(self, *, allow_eviction):
            return RuntimeControllerResponse(
                200,
                {
                    "data": {
                        "target": "provider-owner",
                        "device_id": "provider-device",
                        "state": "available",
                        "configuration": "changed",
                        "verification": "passed",
                        "evicted": [],
                        "report": {"provider": "diagnostics"},
                    },
                    "warnings": [],
                },
            )

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(routes.RuntimeControllerClient, "from_config", lambda: Client())

    response = await _endpoint()(_request())
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body == {
        "data": {
            "state": "available",
            "configuration": "changed",
            "verification": "passed",
            "evicted": [],
        },
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_proxy_rejects_browser_supplied_target(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")

    response = await _endpoint()(_request({"target": "browser-choice", "allow_eviction": False}))

    assert response.status_code == 400
    assert json.loads(response.body)["errors"][0]["code"] == "invalid_request_body"


@pytest.mark.asyncio
async def test_proxy_requires_boolean_eviction_permission(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")

    response = await _endpoint()(_request({"allow_eviction": "yes"}))

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_proxy_is_admin_gated(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    with pytest.raises(Exception) as exc:
        await _endpoint()(_request())

    assert getattr(exc.value, "status_code", None) in {401, 403}
