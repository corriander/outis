from urllib.parse import parse_qs, urlparse

import pytest


class _Response:
    def __init__(self, payload, link=""):
        self._payload = payload
        self.status_code = 200
        self.headers = {"link": link}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_search_is_broad_and_keeps_finetunes_and_incomplete_metadata():
    from services.hwfit.hf_search import search_huggingface_models

    client = _Client(_Response([
        {
            "id": "example/Useful-7B-LoRA",
            "author": "example",
            "downloads": 42,
            "likes": 7,
            "pipeline_tag": "text-generation",
            "tags": ["peft", "lora"],
        },
        {
            "modelId": "example/metadata-light-finetune",
            "downloads": 3,
        },
    ]))

    result = await search_huggingface_models("useful finetune", limit=25, client=client)

    assert [model["repo_id"] for model in result["models"]] == [
        "example/Useful-7B-LoRA",
        "example/metadata-light-finetune",
    ]
    assert result["models"][0]["compatibility"]["status"] == "unknown"
    assert "Adapter or finetune metadata detected" in result["models"][0]["compatibility"]["annotations"]
    assert "Pipeline metadata is missing" in result["models"][1]["compatibility"]["annotations"]

    query = parse_qs(urlparse(client.requests[0][0]).query)
    assert query["search"] == ["useful finetune"]
    assert query["sort"] == ["downloads"]
    assert query["direction"] == ["-1"]
    assert query["limit"] == ["25"]
    assert "filter" not in query
    assert result["ordering"] == "downloads"
    assert result["models"][0]["relevance"]["ordering"] == "downloads"


@pytest.mark.asyncio
async def test_search_exposes_only_the_next_hugging_face_cursor():
    from services.hwfit.hf_search import search_huggingface_models

    next_url = "https://huggingface.co/api/models?cursor=opaque%3Avalue&limit=2"
    client = _Client(_Response([], link=f'<{next_url}>; rel="next"'))

    result = await search_huggingface_models("model", limit=2, client=client)

    assert result["next_cursor"] == "opaque:value"


@pytest.mark.asyncio
async def test_search_route_rejects_empty_queries():
    from routes.hwfit_routes import setup_hwfit_routes

    endpoint = next(
        route.endpoint
        for route in setup_hwfit_routes().routes
        if route.path == "/api/hwfit/discover"
    )

    with pytest.raises(Exception) as exc:
        await endpoint(query="")

    assert getattr(exc.value, "status_code", None) == 400
