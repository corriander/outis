"""Guards for the Extended-search gate and first-class HF+ rows (#11).

Broad Hugging Face discovery is opt-in via the search-type dropdown's
"Extended" option: Standard and Vision must behave exactly like the inherited
browser (no /api/hwfit/discover call), so the broad search can iterate without
ever being a regressive replacement for the existing search.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COOKBOOK = (ROOT / "static/js/cookbook.js").read_text(encoding="utf-8")
HWFIT = (ROOT / "static/js/cookbook-hwfit.js").read_text(encoding="utf-8")
STYLE = (ROOT / "static/style.css").read_text(encoding="utf-8")


def test_usecase_dropdown_offers_extended():
    assert '<option value="extended"' in COOKBOOK
    assert "Extended</option>" in COOKBOOK


def test_discovery_call_is_gated_on_extended_mode():
    assert "const isExtended = useCase === 'extended';" in HWFIT
    assert "if (search && !isImageMode && isExtended) {" in HWFIT
    # The one and only /discover call site sits behind that gate.
    assert HWFIT.count("fetch(`/api/hwfit/discover") == 1


def test_extended_is_not_sent_as_a_catalogue_use_case_filter():
    # "extended" isn't a real use case — sending it would empty the catalogue.
    assert "if (useCase && !isExtended) params.set('use_case', useCase);" in HWFIT


def test_discovery_request_carries_the_hardware_override_params():
    # Same URLSearchParams as the /models call, minus catalogue-only knobs,
    # so search rows rank against the identical (possibly manual) profile.
    assert "const dparams = new URLSearchParams(params);" in HWFIT
    assert "dparams.set('query', search);" in HWFIT
    assert "if (_hfDiscoveryShowAll) dparams.set('show_all', '1');" in HWFIT


def test_enriched_rows_render_first_class_with_provenance():
    # Server-enriched rows pass through row-shaped instead of being rebuilt
    # as dead columns…
    assert "if (m.fit_level) {" in HWFIT
    assert "_enriched: true," in HWFIT
    # …the author/org is visible at a glance on search-sourced rows…
    assert "const _orgPrefix = (m._isDiscovery && _showAuthorPrefix() && m.name?.includes('/'))" in HWFIT
    # …estimates are attributed as estimates, with Hub engagement as context…
    assert "params/quant/VRAM estimated from the repo name" in HWFIT
    assert "downloads, ${m.likes ?? 0} likes" in HWFIT
    # …and enriched rows open the standard expand panel (only unenriched
    # search rows keep the fill-the-download-input shortcut).
    assert "modelData._isOllama || (modelData._isDiscovery && !modelData._enriched)" in HWFIT


def test_zero_engagement_filter_is_stated_not_silent():
    assert "_hfDiscoveryMeta = { hidden: dpage.hidden_count || 0, showAll: _hfDiscoveryShowAll };" in HWFIT
    assert "data-hf-discovery-showall" in HWFIT


def test_badges_survive_long_repo_names():
    # The name TEXT truncates in its own span; badges (HF+ with the
    # downloads/likes tooltip) stay visible outside it. Regression: the whole
    # cell used to ellipsis-clip, hiding the badge on most long-named rows.
    assert '<span class="hwfit-name-text">${_orgPrefix}${esc(_short)}${_quantSuffix}</span>' in HWFIT
    assert ".hwfit-name .hwfit-name-text" in STYLE
    assert "text-overflow: ellipsis" in STYLE


def test_author_toggle_is_extended_only_and_persisted():
    assert 'id="hwfit-author-toggle"' in COOKBOOK
    assert "const _SHOW_AUTHOR_KEY = 'hwfit_show_author_v1';" in HWFIT
    assert "authorBtn.hidden = (uc?.value || '') !== 'extended';" in HWFIT
    # Sits at the END of the filter row (after the Context control) and zeroes
    # the global button margin that knocked it out of line.
    assert COOKBOOK.index('id="hwfit-context-label"') < COOKBOOK.index('id="hwfit-author-toggle"')
    assert "margin: 0; /* a global button rule adds margin-top" in STYLE


def test_manual_hardware_accepts_a_real_gpu_model_name():
    # The name reaches the backend as manual_gpu_name so the bandwidth table
    # can match ("7900 XTX" → 960 GB/s) instead of the FALLBACK_K constants.
    assert 'class="hwfit-manual-gpu-name"' in COOKBOOK
    assert "manual_gpu_name: s.mode === 'gpu' ? String(s.gpuName || '') : ''" in HWFIT


def test_ollama_rows_share_the_backend_search_semantics():
    # Per-token matching + "-token" exclusions, mirroring
    # services/hwfit/fit.py:split_search_terms.
    assert "function _searchMatchRows(rows, search, blobOf)" in HWFIT
    assert "_searchMatchRows(_olRows, search, r => `${r.name} ${r._description || ''}`)" in HWFIT
    assert "exclusions.some(x => blob.includes(x))" in HWFIT


def test_low_engagement_search_rows_dim_but_stay_visible():
    assert "const lowEngagement = m._isDiscovery" in HWFIT
    assert "_LOW_ENGAGEMENT_DOWNLOADS" in HWFIT
    assert ".hwfit-row-lowdl { opacity: 0.5; }" in STYLE
    assert ".hwfit-row-lowdl:hover { opacity: 0.9; }" in STYLE
