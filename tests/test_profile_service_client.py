"""Behavioural tests for the external ProfileService v1 client.

All providers and data here are synthetic. Transport behaviour is exercised
through the real httpx pipeline via ``httpx.MockTransport`` (which honours
``follow_redirects=False``) and, for the ambient-proxy refusal, a real loopback
server. No real service URL, token, model name, or profile is used.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from profile_service.client import (
    MAX_PROFILE_SERVICE_BYTES,
    ProfileServiceClient,
    ProfileServiceError,
    ProfileServiceInvalid,
    ProfileServiceUnauthorized,
    ProfileServiceUnavailable,
    normalise_artifact_ref,
)

TOKEN = "synthetic-bearer-token"
BASE = "https://profile.invalid"

DISCOVERY = {
    "schema_version": 1,
    "service_id": "profile-service-example-1",
    "contract_version": 1,
    "accepted_authorities": ["inventory-example-a"],
    "profiles": {"concurrency": {"etag": True, "if_match_required_on_write": True}},
    "auth": {"mode": "bearer"},
    "form": {"url": "/v1/form"},
}


def _client(handler, **kwargs) -> ProfileServiceClient:
    return ProfileServiceClient(
        BASE, token=TOKEN, transport=httpx.MockTransport(handler), **kwargs
    )


def _json(payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers)


# -- configuration & URL validation ---------------------------------------


def test_from_env_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("OUTIS_PROFILE_SERVICE_URL", raising=False)
    assert ProfileServiceClient.from_env() is None


def test_from_env_reads_url_token_and_name(monkeypatch):
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", "https://profile.invalid/")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TOKEN", "tok")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_NAME", "Studio profiles")
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TIMEOUT", "12")

    client = ProfileServiceClient.from_env()

    assert client is not None
    assert client.base_url == "https://profile.invalid"
    assert client.token == "tok"
    assert client.name == "Studio profiles"
    assert client.timeout_seconds == 12.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.1", 0.5), ("0", 0.5), ("120", 60.0), ("nonsense", 10.0), ("30", 30.0)],
)
def test_timeout_is_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_URL", BASE)
    monkeypatch.setenv("OUTIS_PROFILE_SERVICE_TIMEOUT", raw)
    assert ProfileServiceClient.from_env().timeout_seconds == expected


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://profile.invalid",
        "profile.invalid",
        "https://user:pass@profile.invalid",
        "https://profile.invalid/?q=1",
        "https://profile.invalid/#frag",
        "https:///nohost",
    ],
)
def test_base_url_validation_rejects_unsafe_urls(bad):
    with pytest.raises(ProfileServiceError):
        ProfileServiceClient(bad, token=TOKEN)


def test_base_url_trailing_slash_normalised():
    assert ProfileServiceClient("https://profile.invalid/v1/", token=TOKEN).base_url == (
        "https://profile.invalid/v1"
    )


# -- token handling --------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_token_is_sent_upstream():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return _json(DISCOVERY)

    await _client(handler).get_service()
    assert seen["auth"] == f"Bearer {TOKEN}"


@pytest.mark.asyncio
async def test_no_token_configured_sends_no_authorization_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return _json(DISCOVERY)

    client = ProfileServiceClient(BASE, token=None, transport=httpx.MockTransport(handler))
    await client.get_service()
    assert seen["auth"] is None


# -- discovery & contract --------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_returns_document():
    response = await _client(lambda r: _json(DISCOVERY)).get_service()
    assert response.body["accepted_authorities"] == ["inventory-example-a"]


@pytest.mark.asyncio
async def test_discovery_contract_mismatch_raises_invalid():
    doc = {**DISCOVERY, "contract_version": 2}
    with pytest.raises(ProfileServiceInvalid):
        await _client(lambda r: _json(doc)).get_service()


@pytest.mark.asyncio
async def test_discovery_missing_accepted_authorities_raises_invalid():
    doc = {k: v for k, v in DISCOVERY.items() if k != "accepted_authorities"}
    with pytest.raises(ProfileServiceInvalid):
        await _client(lambda r: _json(doc)).get_service()


@pytest.mark.asyncio
async def test_discovery_tolerates_unknown_additive_fields():
    doc = {**DISCOVERY, "future_field": {"anything": True}}
    response = await _client(lambda r: _json(doc)).get_service()
    assert response.body["future_field"] == {"anything": True}


@pytest.mark.asyncio
async def test_form_requires_fields_list():
    with pytest.raises(ProfileServiceInvalid):
        await _client(lambda r: _json({"form_version": 1})).get_form()


# -- transport failure classes --------------------------------------------


@pytest.mark.asyncio
async def test_upstream_401_raises_unauthorized():
    with pytest.raises(ProfileServiceUnauthorized):
        await _client(lambda r: httpx.Response(401, json={"detail": "no"})).get_service()


@pytest.mark.asyncio
async def test_malformed_json_raises_invalid():
    def handler(request):
        return httpx.Response(200, content=b"{not json", headers={"Content-Type": "application/json"})

    with pytest.raises(ProfileServiceInvalid):
        await _client(handler).get_service()


@pytest.mark.asyncio
async def test_oversize_response_raises_invalid():
    big = b'{"padding":"' + b"x" * (MAX_PROFILE_SERVICE_BYTES + 10) + b'"}'

    def handler(request):
        return httpx.Response(200, content=big, headers={"Content-Type": "application/json"})

    with pytest.raises(ProfileServiceInvalid):
        await _client(handler).list_profiles()


@pytest.mark.asyncio
async def test_redirect_is_refused_not_followed():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/v1/service"})

    with pytest.raises(ProfileServiceInvalid):
        await _client(handler).get_service()


@pytest.mark.asyncio
async def test_network_failure_raises_unavailable():
    # Bind then close a socket to obtain a definitely-closed loopback port.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client = ProfileServiceClient(f"http://127.0.0.1:{port}", token=TOKEN)
    with pytest.raises(ProfileServiceUnavailable):
        await client.get_service()


# -- draft / preview -------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_forwards_artifact_ref_and_drops_model_path():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _json({"data": {"values": {"name": "x"}, "form_version": 1}, "warnings": []})

    await _client(handler).create_draft(
        {
            "authority": "inventory-example-a",
            "artifact_id": "opaque/example.gguf#0123",
            "observation": "obs-1",
            "model_path": r"R:\hostile.gguf",
        }
    )

    ref = captured["body"]["artifact_ref"]
    assert ref == {
        "authority": "inventory-example-a",
        "artifact_id": "opaque/example.gguf#0123",
        "observation": "obs-1",
    }
    assert "model_path" not in ref
    assert captured["body"]["template"] is None


@pytest.mark.asyncio
async def test_draft_preserves_warnings():
    warning = {
        "pointer": "/artifact_ref/observation",
        "code": "artifact_observation_stale",
        "message": "re-observed",
        "meta": {"current": "obs-new"},
    }

    def handler(request):
        return _json({"data": {"values": {}, "form_version": 1}, "warnings": [warning]})

    response = await _client(handler).create_draft(
        {"authority": "inventory-example-a", "artifact_id": "id", "observation": "obs-old"}
    )
    assert response.body["warnings"] == [warning]


@pytest.mark.asyncio
async def test_preview_invalid_values_returned_not_raised():
    envelope = {
        "data": None,
        "errors": [{"pointer": "/values/name", "code": "pattern_mismatch", "message": "bad"}],
        "warnings": [],
    }

    def handler(request):
        return _json(envelope, status=200)

    response = await _client(handler).preview({"name": "bad name!"})
    assert response.status_code == 200
    assert response.body["errors"][0]["code"] == "pattern_mismatch"


# -- CRUD + ETag / If-Match ------------------------------------------------


@pytest.mark.asyncio
async def test_create_captures_etag_and_location():
    def handler(request):
        return _json(
            {"data": {"profile": {"id": "example"}}, "warnings": []},
            status=201,
            headers={"ETag": '"abc123"', "Location": "/v1/profiles/example"},
        )

    response = await _client(handler).create_profile(
        {"authority": "inventory-example-a", "artifact_id": "id"}, {"name": "example"}
    )
    assert response.status_code == 201
    assert response.etag == '"abc123"'
    assert response.location == "/v1/profiles/example"


@pytest.mark.asyncio
async def test_read_captures_etag():
    def handler(request):
        return _json(
            {"data": {"profile": {"id": "example"}}, "warnings": []},
            headers={"ETag": '"live"'},
        )

    response = await _client(handler).read_profile("example")
    assert response.etag == '"live"'


@pytest.mark.asyncio
async def test_patch_forwards_if_match_and_set_clear():
    captured = {}

    def handler(request):
        captured["if_match"] = request.headers.get("If-Match")
        captured["body"] = json.loads(request.content)
        return _json({"data": {"profile": {}}, "warnings": []}, headers={"ETag": '"new"'})

    await _client(handler).patch_profile(
        "example", set_values={"ctx_size": 65536}, clear=["description"], if_match='"abc123"'
    )
    assert captured["if_match"] == '"abc123"'
    assert captured["body"] == {"set": {"ctx_size": 65536}, "clear": ["description"]}


@pytest.mark.asyncio
async def test_write_without_if_match_sends_no_header_and_never_wildcards():
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return _json(
            {"errors": [{"pointer": "/", "code": "precondition_required", "message": "x"}],
             "warnings": []},
            status=428,
        )

    response = await _client(handler).replace_profile("example", {"name": "example"}, if_match=None)
    assert "if-match" not in {k.lower() for k in captured["headers"]}
    # A missing precondition is the provider's 428 to answer, forwarded verbatim.
    assert response.status_code == 428


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [409, 412, 422, 428, 404, 406])
async def test_domain_error_status_and_envelope_preserved(status):
    envelope = {"errors": [{"pointer": "/", "code": "x", "message": "y"}], "warnings": []}
    response = await _client(lambda r: _json(envelope, status=status)).read_profile("p")
    assert response.status_code == status
    assert response.body == envelope


@pytest.mark.asyncio
async def test_delete_204_has_no_body():
    response = await _client(lambda r: httpx.Response(204)).delete_profile("p", if_match='"e"')
    assert response.status_code == 204
    assert response.body is None


# -- artifact_ref normalisation -------------------------------------------


def test_normalise_artifact_ref_keeps_only_wire_keys():
    out = normalise_artifact_ref(
        {"authority": "a", "artifact_id": "b", "observation": "c", "model_path": "R:\\x", "path": "y"}
    )
    assert out == {"authority": "a", "artifact_id": "b", "observation": "c"}


def test_normalise_artifact_ref_rejects_non_mapping():
    with pytest.raises(ProfileServiceError):
        normalise_artifact_ref("not-a-ref")


# -- ambient proxy refusal (real loopback server) -------------------------


class _DiscoveryHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps(DISCOVERY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def discovery_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_ambient_proxy_is_ignored(discovery_server, monkeypatch):
    # trust_env=False means these must be ignored; if honoured, the request
    # would be routed to a dead proxy and fail.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    client = ProfileServiceClient(discovery_server, token=TOKEN)
    response = await client.get_service()
    assert response.body["service_id"] == "profile-service-example-1"
