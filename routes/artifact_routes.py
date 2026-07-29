"""Outis-side routes for external model-artifact inventory providers."""

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from artifact_store.client import ArtifactStoreClient, ArtifactStoreError, ArtifactStoreUnavailable
from src.cookbook_capabilities import require_cookbook_capability


def setup_artifact_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/cookbook/artifacts")
    async def list_artifacts(request: Request):
        require_admin(request)
        require_cookbook_capability("artifact_store", "list")
        client = ArtifactStoreClient.from_env()
        if client is None:
            raise HTTPException(501, "No external ArtifactStore provider is configured")
        try:
            return await client.list_artifacts()
        except ArtifactStoreUnavailable as exc:
            raise HTTPException(502, str(exc)) from exc
        except ArtifactStoreError as exc:
            raise HTTPException(502, str(exc)) from exc

    return router
