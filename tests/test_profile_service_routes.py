"""Behavioural tests for the same-origin ProfileService proxy routes.

The proxy is exercised by invoking the route endpoints directly with synthetic
requests and a fake client, mirroring ``test_artifact_store_routes``. Assertions
are on observable behaviour: status codes, forwarded envelopes, surfaced ETag /
Location, If-Match plumbing, and the guarantee that the server-side bearer token
never appears in a browser-facing response.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

import routes.profile_service_routes as psr
from profile_service.client import (
    ProfileServiceInvalid,
    ProfileServiceResponse,
    ProfileServiceUnauthorized,
    ProfileServiceUnavailable,
)

TOKEN = "server-side-secret-token"


def _endpoint(path: str, method: str):
    return next(
        route.endpoint
        for route in psr.setup_profile_service_routes().routes
        if route.path == path and method in route.methods
    )


def _request(path: str, method: str = "GET", *, headers=None, body=None) -> Request:
    header_list = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": header_list,
        "app": FastAPI(),
    }
    if body is None:
        return Request(scope)
    raw = json.dumps(body).encode() if not isinstance(body, bytes) else body

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def _body(response) -> dict:
    return json.loads(bytes(response.body))


class FakeClient:
    name = "Synthetic profiles"

    def __init__(self, *, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.calls = {}

    async def _answer(self, name, **kw):
        self.calls[name] = kw
        if self._raises is not None:
            raise self._raises
        return self._result

    async def get_service(self):
        return await self._answer("get_service")

    async def get_form(self):
        return await self._answer("get_form")

    async def list_profiles(self):
        return await self._answer("list_profiles")

    async def create_draft(self, artifact_ref):
        return await self._answer("create_draft", artifact_ref=artifact_ref)

    async def preview(self, values):
        return await self._answer("preview", values=values)

    async def create_profile(self, artifact_ref, values):
        return await self._answer("create_profile", artifact_ref=artifact_ref, values=values)

    async def read_profile(self, profile_id):
        return await self._answer("read_profile", profile_id=profile_id)

    async def replace_profile(self, profile_id, values, *, if_match):
        return await self._answer("replace", profile_id=profile_id, values=values, if_match=if_match)

    async def patch_profile(self, profile_id, *, set_values, clear, if_match):
        return await self._answer(
            "patch", profile_id=profile_id, set_values=set_values, clear=clear, if_match=if_match
        )

    async def delete_profile(self, profile_id, *, if_match):
        return await self._answer("delete", profile_id=profile_id, if_match=if_match)


def _install(monkeypatch, client):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(psr.ProfileServiceClient, "from_env", lambda: client)


# -- admin & configuration gates ------------------------------------------


@pytest.mark.asyncio
async def test_route_requires_admin(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.setattr(psr.ProfileServiceClient, "from_env", lambda: FakeClient())
    endpoint = _endpoint("/api/cookbook/profile-service/profiles", "GET")

    with pytest.raises(HTTPException) as exc:
        await endpoint(_request("/api/cookbook/profile-service/profiles"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unconfigured_provider_returns_501(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(psr.ProfileServiceClient, "from_env", lambda: None)
    endpoint = _endpoint("/api/cookbook/profile-service/profiles", "GET")

    response = await endpoint(_request("/api/cookbook/profile-service/profiles"))
    assert response.status_code == 501
    assert _body(response)["errors"][0]["code"] == "profile_service_not_configured"


# -- upstream fault translation -------------------------------------------


@pytest.mark.asyncio
async def test_upstream_401_becomes_502_unauthorized(monkeypatch):
    _install(monkeypatch, FakeClient(raises=ProfileServiceUnauthorized("no")))
    endpoint = _endpoint("/api/cookbook/profile-service", "GET")

    response = await endpoint(_request("/api/cookbook/profile-service"))
    assert response.status_code == 502
    assert _body(response)["errors"][0]["code"] == "profile_service_unauthorized"


@pytest.mark.asyncio
async def test_unreachable_becomes_502(monkeypatch):
    _install(monkeypatch, FakeClient(raises=ProfileServiceUnavailable("down")))
    endpoint = _endpoint("/api/cookbook/profile-service/profiles", "GET")

    response = await endpoint(_request("/api/cookbook/profile-service/profiles"))
    assert response.status_code == 502
    assert _body(response)["errors"][0]["code"] == "profile_service_unreachable"


@pytest.mark.asyncio
async def test_invalid_contract_becomes_502(monkeypatch):
    _install(monkeypatch, FakeClient(raises=ProfileServiceInvalid("bad")))
    endpoint = _endpoint("/api/cookbook/profile-service/form", "GET")

    response = await endpoint(_request("/api/cookbook/profile-service/form"))
    assert response.status_code == 502
    assert _body(response)["errors"][0]["code"] == "profile_service_invalid"


# -- envelope preservation -------------------------------------------------


@pytest.mark.asyncio
async def test_domain_error_envelope_is_preserved(monkeypatch):
    envelope = {
        "errors": [
            {"pointer": "/values/ctx_size", "code": "out_of_range", "message": "too big",
             "meta": {"min": 0, "max": 262144}}
        ],
        "warnings": [],
    }
    _install(monkeypatch, FakeClient(result=ProfileServiceResponse(422, envelope)))
    endpoint = _endpoint("/api/cookbook/profile-service/preview", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/preview", "POST", body={"values": {}})
    )
    assert response.status_code == 422
    assert _body(response) == envelope


@pytest.mark.asyncio
async def test_warnings_are_preserved_on_success(monkeypatch):
    envelope = {
        "data": {"values": {}, "form_version": 1},
        "warnings": [{"pointer": "/artifact_ref/observation", "code": "artifact_observation_stale",
                      "message": "stale"}],
    }
    _install(monkeypatch, FakeClient(result=ProfileServiceResponse(200, envelope)))
    endpoint = _endpoint("/api/cookbook/profile-service/draft", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/draft", "POST",
                 body={"artifact_ref": {"authority": "a", "artifact_id": "b"}})
    )
    assert _body(response)["warnings"][0]["code"] == "artifact_observation_stale"


# -- discovery display name & token confinement ---------------------------


@pytest.mark.asyncio
async def test_discovery_adds_provider_name_and_never_leaks_token(monkeypatch):
    doc = {
        "schema_version": 1,
        "service_id": "profile-service-example-1",
        "contract_version": 1,
        "accepted_authorities": ["inventory-example-a"],
    }
    client = FakeClient(result=ProfileServiceResponse(200, doc, etag=None))
    _install(monkeypatch, client)
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TOKEN", TOKEN)
    endpoint = _endpoint("/api/cookbook/profile-service", "GET")

    response = await endpoint(_request("/api/cookbook/profile-service"))
    payload = _body(response)
    assert payload["provider_name"] == "Synthetic profiles"
    assert payload["service_id"] == "profile-service-example-1"
    # The server-side token must never cross to the browser.
    serialised = json.dumps(payload) + json.dumps({k.decode(): v.decode() for k, v in response.raw_headers})
    assert TOKEN not in serialised


# -- ETag / Location / If-Match plumbing ----------------------------------


@pytest.mark.asyncio
async def test_create_surfaces_etag_and_location(monkeypatch):
    result = ProfileServiceResponse(
        201, {"data": {"profile": {"id": "example"}}, "warnings": []},
        etag='"abc123"', location="/v1/profiles/example",
    )
    _install(monkeypatch, FakeClient(result=result))
    endpoint = _endpoint("/api/cookbook/profile-service/profiles", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/profiles", "POST",
                 body={"artifact_ref": {"authority": "a", "artifact_id": "b"}, "values": {"name": "example"}})
    )
    assert response.status_code == 201
    assert response.headers["etag"] == '"abc123"'
    # The upstream relative Location is rewritten onto this proxy.
    assert response.headers["location"] == "/api/cookbook/profile-service/profiles/example"


@pytest.mark.asyncio
async def test_write_forwards_browser_if_match(monkeypatch):
    client = FakeClient(result=ProfileServiceResponse(200, {"data": {"profile": {}}, "warnings": []}, etag='"new"'))
    _install(monkeypatch, client)
    endpoint = _endpoint("/api/cookbook/profile-service/profiles/{profile_id}", "PUT")

    await endpoint(
        _request("/api/cookbook/profile-service/profiles/example", "PUT",
                 headers={"If-Match": '"abc123"'}, body={"values": {"name": "example"}}),
        profile_id="example",
    )
    assert client.calls["replace"]["if_match"] == '"abc123"'
    assert client.calls["replace"]["profile_id"] == "example"


@pytest.mark.asyncio
async def test_write_without_if_match_forwards_none(monkeypatch):
    client = FakeClient(result=ProfileServiceResponse(428, {"errors": [], "warnings": []}))
    _install(monkeypatch, client)
    endpoint = _endpoint("/api/cookbook/profile-service/profiles/{profile_id}", "PUT")

    await endpoint(
        _request("/api/cookbook/profile-service/profiles/example", "PUT",
                 body={"values": {"name": "example"}}),
        profile_id="example",
    )
    # Never invent a wildcard: absent header forwards as None.
    assert client.calls["replace"]["if_match"] is None


@pytest.mark.asyncio
async def test_patch_forwards_set_and_clear(monkeypatch):
    client = FakeClient(result=ProfileServiceResponse(200, {"data": {"profile": {}}, "warnings": []}, etag='"n"'))
    _install(monkeypatch, client)
    endpoint = _endpoint("/api/cookbook/profile-service/profiles/{profile_id}", "PATCH")

    await endpoint(
        _request("/api/cookbook/profile-service/profiles/example", "PATCH",
                 headers={"If-Match": '"e"'},
                 body={"set": {"ctx_size": 65536}, "clear": ["description"]}),
        profile_id="example",
    )
    assert client.calls["patch"]["set_values"] == {"ctx_size": 65536}
    assert client.calls["patch"]["clear"] == ["description"]
    assert client.calls["patch"]["if_match"] == '"e"'


@pytest.mark.asyncio
async def test_delete_forwards_if_match_and_returns_204(monkeypatch):
    client = FakeClient(result=ProfileServiceResponse(204, None))
    _install(monkeypatch, client)
    endpoint = _endpoint("/api/cookbook/profile-service/profiles/{profile_id}", "DELETE")

    response = await endpoint(
        _request("/api/cookbook/profile-service/profiles/example", "DELETE",
                 headers={"If-Match": '"def456"'}),
        profile_id="example",
    )
    assert response.status_code == 204
    assert client.calls["delete"]["if_match"] == '"def456"'


@pytest.mark.asyncio
async def test_malformed_json_body_returns_400(monkeypatch):
    _install(monkeypatch, FakeClient(result=ProfileServiceResponse(200, {})))
    endpoint = _endpoint("/api/cookbook/profile-service/preview", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/preview", "POST", body=b"{not json")
    )
    assert response.status_code == 400
    assert _body(response)["errors"][0]["code"] == "invalid_json"


@pytest.mark.parametrize(
    ("upstream", "expected"),
    [
        ("/v1/profiles/example", "/api/cookbook/profile-service/profiles/example"),
        ("/v1/profiles/name%20with%20space", "/api/cookbook/profile-service/profiles/name%20with%20space"),
    ],
)
@pytest.mark.asyncio
async def test_relative_profile_location_is_rewritten(monkeypatch, upstream, expected):
    result = ProfileServiceResponse(
        201, {"data": {"profile": {"id": "example"}}, "warnings": []}, location=upstream
    )
    _install(monkeypatch, FakeClient(result=result))
    endpoint = _endpoint("/api/cookbook/profile-service/profiles", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/profiles", "POST",
                 body={"artifact_ref": {"authority": "a", "artifact_id": "b"}, "values": {}})
    )
    assert response.headers["location"] == expected


@pytest.mark.parametrize(
    "upstream",
    [
        "https://evil.invalid/v1/profiles/example",   # absolute / off-origin
        "//evil.invalid/v1/profiles/example",          # protocol-relative
        "/etc/passwd",                                  # unrelated path
        "/v1/profiles/",                                # empty id
        "/v1/profiles/a/b",                             # multi-segment
        "/v1/artifacts/example",                        # unrelated resource
        "/v1/profiles/example?x=1",                     # decorated
    ],
)
@pytest.mark.asyncio
async def test_unsafe_or_unrelated_location_is_suppressed(monkeypatch, upstream):
    result = ProfileServiceResponse(
        201, {"data": {"profile": {"id": "example"}}, "warnings": []}, location=upstream
    )
    _install(monkeypatch, FakeClient(result=result))
    endpoint = _endpoint("/api/cookbook/profile-service/profiles", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/profiles", "POST",
                 body={"artifact_ref": {"authority": "a", "artifact_id": "b"}, "values": {}})
    )
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_invalid_configured_url_returns_502(monkeypatch):
    # Do not patch from_env: let the real constructor reject the bad URL and the
    # proxy map that construction failure into the structured envelope.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", "not a valid url")
    endpoint = _endpoint("/api/cookbook/profile-service", "GET")

    response = await endpoint(_request("/api/cookbook/profile-service"))
    assert response.status_code == 502
    assert _body(response)["errors"][0]["code"] == "profile_service_invalid"


@pytest.mark.parametrize("raw", [b"[]", b"null", b'"string"', b"42"])
@pytest.mark.asyncio
async def test_non_object_json_body_is_rejected(monkeypatch, raw):
    # Valid JSON, but not an object: the routes below index the body as a map.
    _install(monkeypatch, FakeClient(result=ProfileServiceResponse(200, {})))
    endpoint = _endpoint("/api/cookbook/profile-service/preview", "POST")

    response = await endpoint(
        _request("/api/cookbook/profile-service/preview", "POST", body=raw)
    )
    assert response.status_code == 400
    assert _body(response)["errors"][0]["code"] == "invalid_request_body"


@pytest.mark.asyncio
async def test_draft_artifact_ref_is_forwarded(monkeypatch):
    client = FakeClient(result=ProfileServiceResponse(200, {"data": {}, "warnings": []}))
    _install(monkeypatch, client)
    endpoint = _endpoint("/api/cookbook/profile-service/draft", "POST")

    await endpoint(
        _request("/api/cookbook/profile-service/draft", "POST",
                 body={"artifact_ref": {"authority": "inventory-example-a", "artifact_id": "id"}})
    )
    assert client.calls["create_draft"]["artifact_ref"] == {
        "authority": "inventory-example-a", "artifact_id": "id",
    }
