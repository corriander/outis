"""Broad-search hits enrich into first-class, fit-ranked browser rows (#11).

A search hit is a candidate catalogue entry that hasn't been enriched: params,
quant, RAM, context, and use case must come from the SAME heuristics the
hf_discovery dynamic-catalogue path uses — no forked estimates — and ranking
must run against the same (possibly manual) hardware profile as /models.
"""

import pytest


QWOPUS_REPO = "Jackrong/Qwopus3.6-35B-A3B-Coder-OPAL-GGUF"


def _hit(repo_id, **overrides):
    """A hit in the normalised shape services.hwfit.hf_search returns."""
    base = {
        "repo_id": repo_id,
        "name": repo_id,
        "author": repo_id.split("/", 1)[0],
        "url": f"https://huggingface.co/{repo_id}",
        "downloads": 120,
        "likes": 4,
        "pipeline_tag": "text-generation",
        "tags": ["gguf"],
        "last_modified": "2026-07-18T10:00:00.000Z",
        "created_at": "2026-07-01T00:00:00.000Z",
        "gated": False,
        "private": False,
        "library_name": "",
        "params": 0,
        "relevance": {"source": "huggingface", "query": "q", "ordering": "downloads"},
        "compatibility": {"status": "unknown", "annotations": []},
    }
    base.update(overrides)
    return base


def test_search_hit_uses_the_same_heuristics_as_collection_entries():
    """No forked heuristics: a search hit and a collection item for the same
    repo must produce identical estimate fields."""
    from services.hwfit.hf_discovery import _entry_from_collection_item, entry_from_search_hit

    collection_entry = _entry_from_collection_item(
        {},
        {
            "id": QWOPUS_REPO,
            "type": "model",
            "downloads": 120,
            "likes": 4,
            "pipeline_tag": "text-generation",
            "lastModified": "2026-07-18T10:00:00.000Z",
        },
        {},
    )
    search_entry = entry_from_search_hit(_hit(QWOPUS_REPO))

    assert collection_entry is not None and search_entry is not None
    for field in (
        "parameter_count", "parameters_raw", "quantization", "min_ram_gb",
        "recommended_ram_gb", "min_vram_gb", "context_length", "use_case",
        "capabilities", "format", "is_gguf", "is_moe", "active_parameters",
        "hf_downloads", "hf_likes", "release_date",
    ):
        assert search_entry.get(field) == collection_entry.get(field), field

    # The regression case in miniature: name-encoded specs are all recovered.
    assert search_entry["parameter_count"] == "35B"
    assert search_entry["quantization"] == "Q4_K_M"
    assert search_entry["use_case"] == "coding"
    assert search_entry["is_moe"] is True
    assert search_entry["format"] == "gguf"
    # Search provenance: the author is the provider ("which qwopus repos are
    # Jackrong's" must be answerable), and the source is marked.
    assert search_entry["provider"] == "Jackrong"
    assert search_entry["_source"] == "hf_search"


def test_hub_declared_safetensors_params_beat_the_name_parse():
    from services.hwfit.hf_discovery import entry_from_search_hit

    entry = entry_from_search_hit(_hit("acme/mystery-instruct", params=8_030_000_000))
    assert entry is not None
    assert entry["parameter_count"] == "8.03B"


def test_unparseable_hits_return_none_rather_than_an_invented_estimate():
    from services.hwfit.hf_discovery import entry_from_search_hit

    assert entry_from_search_hit(_hit("acme/mystery-instruct")) is None


def test_vlm_pipeline_tag_does_not_clamp_context():
    """Regression: 'image-text-to-text' (a vision-language CHAT model) used to
    match the substring "image" and clamp context to 4k — so the same model
    ranked differently depending on which quant upload carried complete
    metadata. Properly-tagged and untagged uploads must now agree."""
    from services.hwfit.hf_discovery import entry_from_search_hit

    tagged = entry_from_search_hit(_hit("unsloth/Qwen3.6-27B-GGUF", pipeline_tag="image-text-to-text"))
    untagged = entry_from_search_hit(_hit("Intel/Qwen3.6-27B-int4", pipeline_tag=""))
    assert tagged["context_length"] == untagged["context_length"] == 32768

    # Genuinely short-context pipelines keep the small default…
    tts = entry_from_search_hit(_hit("acme/some-voice-9B", pipeline_tag="text-to-speech"))
    assert tts["context_length"] == 4096
    # …as do name-marked speech/vision models.
    whisper = entry_from_search_hit(_hit("acme/whisper-large-9B"))
    assert whisper["context_length"] == 4096


def test_mlx_community_hits_take_the_mlx_estimation_path():
    from services.hwfit.hf_discovery import entry_from_search_hit

    entry = entry_from_search_hit(_hit("mlx-community/Qwen3-8B-4bit"))
    assert entry is not None
    assert entry["quantization"] == "mlx-4bit"
    assert entry["format"] == "mlx"
    assert entry["mlx_only"] is True


def _discover_endpoint():
    from routes.hwfit_routes import setup_hwfit_routes

    return next(
        route.endpoint
        for route in setup_hwfit_routes().routes
        if route.path == "/api/hwfit/discover"
    )


def _patch_search(monkeypatch, hits):
    import services.hwfit.hf_search as hf_search

    async def fake_search(query, *, limit=50, cursor="", client=None):
        return {
            "query": query,
            "ordering": "downloads",
            "models": [dict(h) for h in hits],
            "next_cursor": "",
        }

    monkeypatch.setattr(hf_search, "search_huggingface_models", fake_search)


def _patch_system(monkeypatch, ram_gb=32):
    import services.hwfit.hardware as hardware

    def fake_detect(host="", ssh_port="", platform="", fresh=False, **_kw):
        return {
            "has_gpu": False,
            "gpu_name": None,
            "gpu_vram_gb": 0,
            "gpu_count": 0,
            "gpus": [],
            "gpu_groups": [],
            "available_ram_gb": ram_gb,
            "total_ram_gb": ram_gb,
            "backend": "cpu_x86",
            "platform": "linux",
        }

    monkeypatch.setattr(hardware, "detect_system", fake_detect)


@pytest.mark.asyncio
async def test_discover_enriches_hits_and_states_the_zero_engagement_filter(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "native")
    _patch_search(monkeypatch, [
        _hit(QWOPUS_REPO),
        _hit("someone/opus-mt-en-fr-finetuned", downloads=0, likes=0, pipeline_tag="translation"),
        _hit("acme/mystery-model", downloads=9, likes=0),
    ])
    _patch_system(monkeypatch, ram_gb=64)

    page = await _discover_endpoint()(query="qwopus")

    assert page["enriched"] is True
    # Zero-download/zero-like scraps (the "opus" junk class) are dropped as a
    # STATED filter, not silent exclusion.
    assert page["hidden_count"] == 1
    names = [m.get("name") or m.get("repo_id") for m in page["models"]]
    assert "someone/opus-mt-en-fr-finetuned" not in names

    enriched = next(m for m in page["models"] if m["name"] == QWOPUS_REPO)
    # Row-shaped exactly like a catalogue analysis result…
    assert enriched["fit_level"] in {"perfect", "good", "marginal", "too_tight"}
    assert enriched["quant"] == "Q4_K_M"
    assert enriched["parameter_count"] == "35B"
    assert enriched["required_gb"] > 0
    assert isinstance(enriched["score"], (int, float))
    # …with search provenance kept distinguishable from curated values.
    assert enriched["provider"] == "Jackrong"
    assert enriched["_estimated"] is True
    assert enriched["_source"] == "hf_search"
    assert enriched["downloads"] == 120
    assert enriched["repo_id"] == QWOPUS_REPO

    # A hit with no parameter estimate stays a raw, honestly-unassessed row.
    raw = next(m for m in page["models"] if m["repo_id"] == "acme/mystery-model")
    assert "fit_level" not in raw
    assert raw["compatibility"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_discover_show_all_includes_zero_engagement_repos(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "native")
    _patch_search(monkeypatch, [
        _hit("someone/opus-mt-en-fr-finetuned", downloads=0, likes=0, pipeline_tag="translation"),
    ])
    _patch_system(monkeypatch)

    page = await _discover_endpoint()(query="opus", show_all=True)

    assert page["hidden_count"] == 0
    assert [m.get("repo_id") or m.get("name") for m in page["models"]] == [
        "someone/opus-mt-en-fr-finetuned",
    ]


@pytest.mark.asyncio
async def test_discover_ranks_against_the_manual_hardware_profile(monkeypatch):
    """The hardware profile is manually entered on the reference deployment —
    search rows must rank against the same overrides /models sees."""
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "native")
    _patch_search(monkeypatch, [_hit(QWOPUS_REPO)])
    _patch_system(monkeypatch, ram_gb=8)

    endpoint = _discover_endpoint()
    tight = await endpoint(query="qwopus", manual_mode="ram", manual_ram_gb="4")
    roomy = await endpoint(query="qwopus", manual_mode="ram", manual_ram_gb="256")

    assert tight["models"][0]["fit_level"] == "too_tight"
    assert roomy["models"][0]["fit_level"] != "too_tight"


@pytest.mark.asyncio
async def test_manual_gpu_model_name_unlocks_the_bandwidth_speed_path(monkeypatch):
    """A named GPU ("7900 XTX") matches the speed model's bandwidth table;
    an anonymous simulated GPU falls back to crude per-backend constants.
    Same VRAM, wildly different (and more honest) t/s."""
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "native")
    _patch_search(monkeypatch, [_hit(QWOPUS_REPO)])
    _patch_system(monkeypatch, ram_gb=64)

    endpoint = _discover_endpoint()
    kwargs = dict(query="qwopus", manual_mode="gpu", manual_gpu_count="1",
                  manual_vram_gb="24", manual_ram_gb="64", manual_backend="rocm")
    anonymous = await endpoint(**kwargs)
    named = await endpoint(**kwargs, manual_gpu_name="7900 XTX")

    anon_tps = anonymous["models"][0]["speed_tps"]
    named_tps = named["models"][0]["speed_tps"]
    assert named_tps > anon_tps * 2, (anon_tps, named_tps)


def test_apply_manual_hardware_uses_the_given_gpu_model_name():
    from routes.hwfit_routes import _apply_manual_hardware
    from services.hwfit.fit import _lookup_bandwidth

    system = _apply_manual_hardware(
        {"backend": "cpu_x86", "available_ram_gb": 64, "total_ram_gb": 64},
        manual_mode="gpu", manual_gpu_count="2", manual_vram_gb="24",
        manual_backend="rocm", manual_gpu_name="RX 7900 XTX",
    )
    assert system["gpu_name"] == "RX 7900 XTX × 2"
    assert _lookup_bandwidth(system) == 960
    # Anonymous profiles keep the simulated label (and no bandwidth match).
    system = _apply_manual_hardware(
        {"backend": "cpu_x86", "available_ram_gb": 64, "total_ram_gb": 64},
        manual_mode="gpu", manual_gpu_count="1", manual_vram_gb="24", manual_backend="rocm",
    )
    assert system["gpu_name"] == "Simulated ROCM GPU"
    assert _lookup_bandwidth(system) is None


@pytest.mark.asyncio
async def test_discover_stays_raw_in_external_mode(monkeypatch):
    """Ranking against local hardware is runtime_controller territory — the
    capability boundary keeps external-mode hits honestly unassessed."""
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    _patch_search(monkeypatch, [_hit(QWOPUS_REPO)])
    _patch_system(monkeypatch)

    page = await _discover_endpoint()(query="qwopus")

    assert page["enriched"] is False
    assert all("fit_level" not in m for m in page["models"])
    assert page["models"][0]["compatibility"]["status"] == "unknown"
