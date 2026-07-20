import threading
from http.server import ThreadingHTTPServer

import pytest

from artifact_store.client import ArtifactStoreClient, ArtifactStoreUnavailable
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
