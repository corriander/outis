"""Contract checks for the external RuntimeController client."""

import httpx
import pytest
from runtime_controller.client import (
    RuntimeControllerClient,
    RuntimeControllerInvalid,
    RuntimeControllerUnauthorized,
)

DISCOVERY = {
    "schema_version": 1,
    "service_id": "controller-example",
    "contract_version": 1,
    "devices": {
        "restart": {
            "method": "POST",
            "url_template": "/v1/devices/{noun}/restart",
            "allow_eviction_default": False,
        }
    },
}


def _client(handler):
    return RuntimeControllerClient(
        "http://controller.test:8850",
        "opaque target",
        token="secret-token",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_discovery_requires_the_protected_restart_contract():
    async def handler(request):
        assert request.url == httpx.URL("http://controller.test:8850/v1/service")
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(200, json=DISCOVERY)

    response = await _client(handler).get_service()

    assert response.body == DISCOVERY


@pytest.mark.asyncio
async def test_discovery_rejects_an_unsafe_default():
    document = {
        **DISCOVERY,
        "devices": {"restart": {**DISCOVERY["devices"]["restart"], "allow_eviction_default": True}},
    }

    with pytest.raises(RuntimeControllerInvalid, match="supported restart"):
        await _client(lambda request: httpx.Response(200, json=document)).get_service()


@pytest.mark.asyncio
async def test_apply_encodes_configured_target_and_defaults_to_protected():
    async def handler(request):
        assert request.url == httpx.URL(
            "http://controller.test:8850/v1/devices/opaque%20target/restart"
        )
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.headers.get("Proxy-Authorization") is None
        assert request.content == b'{"allow_eviction":false}'
        return httpx.Response(
            200,
            json={
                "data": {
                    "target": "resolved-owner",
                    "state": "available",
                    "configuration": "changed",
                    "verification": "passed",
                    "evicted": [],
                    "report": {},
                },
                "warnings": [],
            },
        )

    response = await _client(handler).apply_profiles()

    assert response.status_code == 200
    assert response.body["data"]["configuration"] == "changed"


@pytest.mark.asyncio
async def test_would_evict_envelope_is_preserved_for_the_browser_decision():
    body = {
        "errors": [
            {
                "pointer": "/",
                "code": "would_evict",
                "message": "restart would evict loaded work",
                "meta": {
                    "models": ["loaded-model"],
                    "retry": {"allow_eviction": True},
                },
            }
        ],
        "warnings": [],
    }

    response = await _client(lambda request: httpx.Response(409, json=body)).apply_profiles()

    assert response.status_code == 409
    assert response.body == body


@pytest.mark.asyncio
async def test_continue_retry_sends_explicit_eviction_permission():
    async def handler(request):
        assert request.content == b'{"allow_eviction":true}'
        return httpx.Response(
            200,
            json={
                "data": {
                    "target": "resolved-owner",
                    "state": "available",
                    "configuration": "unchanged",
                    "verification": "not_applicable",
                    "evicted": ["loaded-model"],
                    "report": {},
                },
                "warnings": [],
            },
        )

    response = await _client(handler).apply_profiles(allow_eviction=True)

    assert response.body["data"]["evicted"] == ["loaded-model"]


@pytest.mark.asyncio
async def test_upstream_unauthorized_is_a_server_side_credential_fault():
    with pytest.raises(RuntimeControllerUnauthorized):
        await _client(lambda request: httpx.Response(401)).apply_profiles()


@pytest.mark.asyncio
async def test_unstructured_error_is_not_forwarded():
    with pytest.raises(RuntimeControllerInvalid, match="malformed restart error"):
        await _client(
            lambda request: httpx.Response(500, json={"detail": "internal"})
        ).apply_profiles()
