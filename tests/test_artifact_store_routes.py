import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/cookbook/artifacts",
        "headers": [],
        "app": FastAPI(),
    })


def _endpoint():
    from routes.artifact_routes import setup_artifact_routes

    return next(route.endpoint for route in setup_artifact_routes().routes)


@pytest.mark.asyncio
async def test_inventory_route_returns_external_provider_document(monkeypatch):
    import routes.artifact_routes as artifact_routes

    document = {
        "schema_version": 1,
        "provider": {"id": "directory", "name": "Directory inventory"},
        "status": {"state": "ready", "sources": []},
        "artifacts": [],
    }

    class FakeClient:
        async def list_artifacts(self):
            return document

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_URL", "http://provider.invalid:7331")
    monkeypatch.setattr(artifact_routes.ArtifactStoreClient, "from_config", lambda: FakeClient())

    assert await _endpoint()(_request()) == document


@pytest.mark.asyncio
async def test_inventory_route_surfaces_provider_unreachable_without_affecting_catalogue(monkeypatch):
    import routes.artifact_routes as artifact_routes

    class OfflineClient:
        async def list_artifacts(self):
            raise artifact_routes.ArtifactStoreUnavailable("ArtifactStore provider is unreachable")

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    monkeypatch.setenv("OUTIS_ARTIFACT_STORE_URL", "http://provider.invalid:7331")
    monkeypatch.setattr(artifact_routes.ArtifactStoreClient, "from_config", lambda: OfflineClient())

    with pytest.raises(HTTPException) as exc:
        await _endpoint()(_request())

    assert exc.value.status_code == 502
    assert "unreachable" in exc.value.detail
    from src.cookbook_capabilities import cookbook_capabilities
    assert cookbook_capabilities()["capabilities"]["catalogue"]["browse"] is True


@pytest.mark.asyncio
async def test_unconfigured_external_inventory_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    monkeypatch.delenv("OUTIS_ARTIFACT_STORE_URL", raising=False)

    with pytest.raises(HTTPException) as exc:
        await _endpoint()(_request())

    assert exc.value.status_code == 501
    assert "artifact_store.list" in exc.value.detail
