"""Pure frontend behavior for provider-backed Cookbook inventory."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "cookbookInventory.js"
HAS_NODE = shutil.which("node") is not None


def _run(expression: str):
    script = (
        f"import {{ artifactDisplayLabels, artifactPathVariantValue, artifactPathVariantsHtml, artifactSplitLabel, formatArtifactBytes, filterInventoryArtifacts, inventorySourceIssues, parseInventoryDocument, sortInventoryArtifacts }} from '{MODULE.as_uri()}';"
        f"console.log(JSON.stringify({expression}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_size_formatter_uses_binary_units():
    assert _run("formatArtifactBytes(5 * 1024 ** 3)") == "5.0 GB"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_inventory_search_matches_location_and_quantisation():
    artifacts = [
        {
            "id": "one",
            "filename": "Model-Q4_K_M.gguf",
            "display_location": "Local models / gemma / Model-Q4_K_M.gguf",
            "group_path": ["gemma"],
            "observed": {"format": "gguf", "quantization": "Q4_K_M", "state": "ready"},
        },
        {
            "id": "two",
            "filename": "Other-Q8_0.gguf",
            "display_location": "Local models / qwen / Other-Q8_0.gguf",
            "group_path": ["qwen"],
            "observed": {"format": "gguf", "quantization": "Q8_0", "state": "ready"},
        },
    ]
    expression = f"filterInventoryArtifacts({json.dumps(artifacts)}, 'gemma q4').map(x => x.id)"

    assert _run(expression) == ["one"]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_inventory_sort_supports_path_then_filename_or_filename_then_path():
    artifacts = [
        {
            "id": "zulu-alpha",
            "filename": "Alpha-10.gguf",
            "display_location": "Local models / zulu / Alpha-10.gguf",
            "group_path": ["zulu"],
        },
        {
            "id": "alpha-beta",
            "filename": "Beta.gguf",
            "display_location": "Local models / alpha / Beta.gguf",
            "group_path": ["alpha"],
        },
        {
            "id": "alpha-alpha",
            "filename": "Alpha-2.gguf",
            "display_location": "Local models / alpha / Alpha-2.gguf",
            "group_path": ["alpha"],
        },
    ]
    encoded = json.dumps(artifacts)

    path_order = _run(
        f"sortInventoryArtifacts({encoded}, 'path').map(x => x.id)"
    )
    filename_order = _run(
        f"sortInventoryArtifacts({encoded}, 'filename').map(x => x.id)"
    )

    assert path_order == ["alpha-alpha", "alpha-beta", "zulu-alpha"]
    assert filename_order == ["alpha-alpha", "zulu-alpha", "alpha-beta"]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_inventory_primary_label_follows_the_selected_sort():
    artifact = {
        "filename": "Model-Q4_K_M.gguf",
        "display_location": "Local models / family / Model-Q4_K_M.gguf",
        "group_path": ["family"],
    }
    encoded = json.dumps(artifact)

    assert _run(f"artifactDisplayLabels({encoded}, 'path')") == {
        "primary": "family / Model-Q4_K_M.gguf",
        "secondary": "Local models / family / Model-Q4_K_M.gguf",
    }
    assert _run(f"artifactDisplayLabels({encoded}, 'filename')") == {
        "primary": "Model-Q4_K_M.gguf",
        "secondary": "Local models / family / Model-Q4_K_M.gguf",
    }
    root_artifact = json.dumps({
        "filename": "Root-Q6_K.gguf",
        "display_location": "Local models / Root-Q6_K.gguf",
        "group_path": [],
    })
    assert _run(f"artifactDisplayLabels({root_artifact}, 'path')")["primary"] == "Root-Q6_K.gguf"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_logical_path_is_primary_and_preserves_arbitrary_nested_hierarchy():
    artifact = {
        "filename": "Model-Q4_K_M.gguf",
        "logical_path": "publisher/family/variant/Model-Q4_K_M.gguf",
        "display_location": "Inventory A / publisher / family / variant / Model-Q4_K_M.gguf",
        "group_path": ["stale", "fallback"],
    }

    assert _run(f"artifactDisplayLabels({json.dumps(artifact)}, 'path')")["primary"] == (
        "publisher / family / variant / Model-Q4_K_M.gguf"
    )


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_path_variant_value_is_not_parsed_or_translated():
    value = r"\\host\share\models\family\Model-Q4_K_M.gguf?raw=a&b=<c>"
    artifact = {"path_variants": [{"label": "Exact", "value": value}]}
    encoded = json.dumps(artifact)

    assert _run(f"artifactPathVariantValue({encoded}, 0)") == value
    html = _run(f"artifactPathVariantsHtml({encoded})")
    assert "Exact" in html
    assert "&amp;" in html
    assert "&lt;c&gt;" in html


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_missing_path_variants_render_no_extra_detail():
    assert _run("artifactPathVariantsHtml({id:'artifact-only'})") == ""


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_inventory_contract_rejects_unidentified_artifacts():
    assert _run("parseInventoryDocument({schema_version:1,artifacts:[{filename:'missing-id.gguf'}]})") is None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_split_label_reports_a_known_expected_part_count():
    artifact = {"observed": {"split": {"parts_present": 1, "parts_expected": 3}}}
    assert _run(f"artifactSplitLabel({json.dumps(artifact)})") == "1/3 parts"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_absent_expected_part_count_is_unknown_not_zero():
    """A provider omits parts_expected when the parts disagree about the total.

    Coercing that absence to zero renders "2/0 parts", which asserts a total the
    provider never claimed.
    """
    artifact = {"observed": {"split": {"parts_present": 2}}}
    assert _run(f"artifactSplitLabel({json.dumps(artifact)})") == "2/? parts"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_artifacts_without_split_metadata_have_no_split_label():
    assert _run("artifactSplitLabel({observed:{size_bytes:1024}})") == ""


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_degraded_sources_surface_the_provider_supplied_reason():
    document = {
        "schema_version": 1,
        "artifacts": [],
        "status": {
            "state": "partial",
            "sources": [
                {"id": "a", "label": "Ready source", "state": "ready"},
                {
                    "id": "b",
                    "label": "Blocked source",
                    "state": "unreachable",
                    "error": "permission denied",
                },
            ],
        },
    }
    issues = _run(f"inventorySourceIssues({json.dumps(document)})")
    assert issues == [
        {"label": "Blocked source", "state": "unreachable", "error": "permission denied"}
    ]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_degraded_source_without_a_reason_is_still_reported():
    document = {
        "schema_version": 1,
        "artifacts": [],
        "status": {"sources": [{"id": "b", "state": "partial"}]},
    }
    assert _run(f"inventorySourceIssues({json.dumps(document)})") == [
        {"label": "b", "state": "partial", "error": ""}
    ]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_path_variants_are_dynamic_and_preserve_provider_order():
    artifact = {
        "path_variants": [
            {"label": "Win11", "value": r"R:\models\example.gguf"},
            {"label": "WSL", "value": "/mnt/r/models/example.gguf"},
            {"label": "Remote shell", "value": "models@example:/srv/models/example.gguf"},
        ]
    }
    encoded = json.dumps(artifact)

    html = _run(f"artifactPathVariantsHtml({encoded})")
    assert html.index("Win11") < html.index("WSL") < html.index("Remote shell")
    assert _run(f"artifactPathVariantValue({encoded}, 2)") == (
        "models@example:/srv/models/example.gguf"
    )
