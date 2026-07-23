import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from artifact_store.client import (
    ArtifactStoreClient,
    ArtifactStoreError,
    ArtifactStoreUnavailable,
    MAX_INVENTORY_BYTES,
)
from artifact_store.directory_provider import DirectoryRoot, make_handler


@pytest.fixture
def artifact_provider(tmp_path):
    (tmp_path / "family").mkdir()
    (tmp_path / "family" / "Example-Q4_K_M.gguf").write_bytes(b"gguf")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            [DirectoryRoot("models", tmp_path, "Local models")],
            "directory",
            "Directory inventory",
            "test-token",
        ),
    )
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
async def test_client_consumes_reference_provider_over_http(artifact_provider):
    client = ArtifactStoreClient(artifact_provider, token="test-token")

    document = await client.list_artifacts()

    assert document["provider"]["id"] == "directory"
    assert [item["filename"] for item in document["artifacts"]] == ["Example-Q4_K_M.gguf"]


@pytest.mark.asyncio
async def test_client_reports_reference_provider_auth_failure(artifact_provider):
    client = ArtifactStoreClient(artifact_provider, token="wrong-token")

    with pytest.raises(ArtifactStoreUnavailable) as exc:
        await client.list_artifacts()

    assert "HTTP 401" in str(exc.value)


# -- streamed response-size limit -----------------------------------------


class _ChunkStream(httpx.AsyncByteStream):
    """A response body that streams pre-set chunks with no Content-Length."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        pass


def _client(handler):
    return ArtifactStoreClient(
        "http://provider.test", token="test-token", transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_declared_content_length_over_limit_is_rejected(monkeypatch):
    monkeypatch.setattr("artifact_store.client.MAX_INVENTORY_BYTES", 500)

    def handler(request):
        return httpx.Response(
            200, headers={"Content-Length": "999999"}, stream=_ChunkStream([b"{}"])
        )

    with pytest.raises(ArtifactStoreError):
        await _client(handler).list_artifacts()


@pytest.mark.asyncio
async def test_streamed_body_over_limit_without_content_length_is_rejected(monkeypatch):
    monkeypatch.setattr("artifact_store.client.MAX_INVENTORY_BYTES", 500)

    def handler(request):
        return httpx.Response(200, stream=_ChunkStream([b"x" * 300, b"y" * 300]))

    with pytest.raises(ArtifactStoreError):
        await _client(handler).list_artifacts()


@pytest.mark.asyncio
async def test_body_just_below_limit_is_read(monkeypatch):
    monkeypatch.setattr("artifact_store.client.MAX_INVENTORY_BYTES", 500)
    document = {"schema_version": 1, "provider": {"id": "directory"}, "artifacts": []}

    def handler(request):
        return httpx.Response(200, json=document)

    result = await _client(handler).list_artifacts()

    assert result == document
