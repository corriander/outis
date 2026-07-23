"""Search exclusion tokens ("opus -mt") across catalogue and broad discovery.

The Hub API has no negation syntax, so the discover route must strip
exclusions before querying Hugging Face and apply them to the returned hits;
the catalogue matcher applies them directly. Semantics match everywhere: any
exclusion match drops the row, every positive term must still match.
"""

import pytest


def test_split_search_terms_separates_exclusions():
    from services.hwfit.fit import split_search_terms

    assert split_search_terms("opus -mt -finetuned") == (["opus"], ["mt", "finetuned"])
    assert split_search_terms("qwen 27b") == (["qwen", "27b"], [])
    assert split_search_terms("- opus") == (["opus"], [])  # bare dash ignored
    assert split_search_terms("") == ([], [])


def test_catalogue_matcher_honours_exclusions():
    from services.hwfit.fit import _matches_search

    opus_mt = {"name": "Helsinki-NLP/opus-mt-en-fr", "provider": "Helsinki-NLP"}
    opus_ft = {"name": "someone/gemma4-opus-finetuned", "provider": "someone"}

    assert _matches_search(opus_mt, "opus") is True
    assert _matches_search(opus_mt, "opus -mt") is False
    assert _matches_search(opus_ft, "opus -mt") is True
    # Exclusion-only search: keep everything except matches.
    assert _matches_search(opus_mt, "-helsinki") is False
    assert _matches_search(opus_ft, "-helsinki") is True


@pytest.mark.asyncio
async def test_discover_strips_exclusions_from_hf_query_and_filters_hits(monkeypatch):
    import services.hwfit.hf_search as hf_search
    from routes.hwfit_routes import setup_hwfit_routes

    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")  # raw path: no hardware needed
    seen_queries = []

    async def fake_search(query, *, limit=50, cursor="", client=None):
        seen_queries.append(query)
        return {
            "query": query,
            "ordering": "downloads",
            "models": [
                {"repo_id": "Helsinki-NLP/opus-mt-en-fr", "author": "Helsinki-NLP",
                 "downloads": 10, "likes": 1, "pipeline_tag": "translation", "tags": []},
                {"repo_id": "someone/gemma4-opus-finetuned", "author": "someone",
                 "downloads": 10, "likes": 1, "pipeline_tag": "text-generation", "tags": []},
            ],
            "next_cursor": "",
        }

    monkeypatch.setattr(hf_search, "search_huggingface_models", fake_search)
    endpoint = next(
        route.endpoint
        for route in setup_hwfit_routes().routes
        if route.path == "/api/hwfit/discover"
    )

    page = await endpoint(query="opus -mt")

    # Only the positive term reached Hugging Face…
    assert seen_queries == ["opus"]
    # …and the excluded hit never reached the browser.
    assert [m["repo_id"] for m in page["models"]] == ["someone/gemma4-opus-finetuned"]


@pytest.mark.asyncio
async def test_discover_rejects_exclusion_only_queries():
    from routes.hwfit_routes import setup_hwfit_routes

    endpoint = next(
        route.endpoint
        for route in setup_hwfit_routes().routes
        if route.path == "/api/hwfit/discover"
    )

    with pytest.raises(Exception) as exc:
        await endpoint(query="-mt")

    assert getattr(exc.value, "status_code", None) == 400
