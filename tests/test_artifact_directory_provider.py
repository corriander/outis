import json
from pathlib import Path

import pytest

from artifact_store.directory_provider import (
    DirectoryRoot,
    PathVariantTemplate,
    inventory_document,
    scan_directory_root,
)


def test_directory_provider_enumerates_only_gguf_paths(tmp_path):
    (tmp_path / "Root-Q4_K_M.gguf").write_bytes(b"root")
    family = tmp_path / "family"
    family.mkdir()
    (family / "Nested-Q8_0.GGUF").write_bytes(b"nested")
    (family / "weights.bin").write_bytes(b"ignored")
    (family / "preview.png").write_bytes(b"ignored")
    (family / "preset.kcpps").write_text("ignored", encoding="utf-8")

    artifacts, source = scan_directory_root(DirectoryRoot("models", tmp_path, "Local models"))

    assert source == {"id": "models", "label": "Local models", "state": "ready", "artifact_count": 2}
    assert [item["filename"] for item in artifacts] == ["Nested-Q8_0.GGUF", "Root-Q4_K_M.gguf"]
    nested = artifacts[0]
    assert nested["logical_path"] == "family/Nested-Q8_0.GGUF"
    assert nested["display_location"] == "Local models / family / Nested-Q8_0.GGUF"
    assert nested["group_path"] == ["family"]
    assert nested["source_id"] == "models"
    assert nested["observation"]
    assert "path_variants" not in nested
    assert nested["observed"]["format"] == "gguf"
    assert nested["observed"]["quantization"] == "Q8_0"
    assert nested["observed"]["state"] == "ready"


def test_split_gguf_is_one_artifact_and_missing_parts_are_incomplete(tmp_path):
    complete = tmp_path / "complete"
    complete.mkdir()
    (complete / "Example-Q4_K_M-00001-of-00002.gguf").write_bytes(b"one")
    (complete / "Example-Q4_K_M-00002-of-00002.gguf").write_bytes(b"two")
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "Large-IQ4_XS-00001-of-00003.gguf").write_bytes(b"one")
    (incomplete / "Large-IQ4_XS-00003-of-00003.gguf").write_bytes(b"three")

    artifacts, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "Models"))
    by_group = {item["group_path"][0]: item for item in artifacts}

    assert len(artifacts) == 2
    assert by_group["complete"]["observed"]["state"] == "ready"
    assert by_group["complete"]["observed"]["size_bytes"] == 6
    assert by_group["complete"]["observed"]["split"] == {
        "parts_present": 2,
        "parts_expected": 2,
    }
    assert by_group["incomplete"]["observed"]["state"] == "incomplete"
    assert by_group["incomplete"]["observed"]["split"] == {
        "parts_present": 2,
        "parts_expected": 3,
    }


def test_temporary_gguf_is_visible_but_not_ready(tmp_path):
    path = tmp_path / "Downloading-Q6_K.gguf.incomplete"
    path.write_bytes(b"partial")

    artifacts, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "Models"))

    assert len(artifacts) == 1
    assert artifacts[0]["filename"] == "Downloading-Q6_K.gguf"
    assert artifacts[0]["files"][0]["filename"] == "Downloading-Q6_K.gguf.incomplete"
    assert artifacts[0]["observed"]["state"] == "incomplete"


def test_completed_monolithic_gguf_supersedes_stale_temporary_file(tmp_path):
    (tmp_path / "Model-Q4_K_M.gguf").write_bytes(b"complete")
    (tmp_path / "Model-Q4_K_M.gguf.incomplete").write_bytes(b"stale")
    root = DirectoryRoot(
        "models",
        tmp_path,
        "Models",
        (PathVariantTemplate("Observed", "{path}"),),
    )

    artifacts, source = scan_directory_root(root)

    assert len(artifacts) == 1
    assert source["artifact_count"] == 1
    assert artifacts[0]["filename"] == "Model-Q4_K_M.gguf"
    assert artifacts[0]["observed"]["state"] == "ready"
    assert artifacts[0]["observed"]["size_bytes"] == len(b"complete")
    assert [item["filename"] for item in artifacts[0]["files"]] == ["Model-Q4_K_M.gguf"]
    assert artifacts[0]["path_variants"][0]["value"].endswith("Model-Q4_K_M.gguf")


def test_multiple_temporary_suffixes_collapse_to_one_monolithic_artifact(tmp_path):
    (tmp_path / "Model-Q5_K_M.gguf.part").write_bytes(b"part")
    (tmp_path / "Model-Q5_K_M.gguf.incomplete").write_bytes(b"incomplete")

    artifacts, source = scan_directory_root(DirectoryRoot("models", tmp_path, "Models"))

    assert len(artifacts) == 1
    assert source["artifact_count"] == 1
    assert artifacts[0]["observed"]["state"] == "incomplete"


def test_artifact_id_is_stable_for_the_same_provider_path(tmp_path):
    path = tmp_path / "family" / "Model-Q5_K_M.gguf"
    path.parent.mkdir()
    path.write_bytes(b"first")
    first, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "First label"))
    relabeled, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "Renamed label"))
    path.write_bytes(b"different contents")
    replaced, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "Renamed label"))

    assert first[0]["id"] == relabeled[0]["id"] == replaced[0]["id"]
    assert first[0]["observation"] == relabeled[0]["observation"]
    assert first[0]["observation"] != replaced[0]["observation"]
    assert first[0]["display_location"] != relabeled[0]["display_location"]


def test_programmatic_inventory_retains_legacy_authority_without_exposing_scan_root(tmp_path):
    (tmp_path / "Model-Q4_K_M.gguf").write_bytes(b"gguf")

    document = inventory_document([DirectoryRoot("models", tmp_path, "Local models")])
    encoded = json.dumps(document)

    assert str(tmp_path) not in encoded
    assert document["provider"] == {
        "id": "directory",
        "name": "Directory inventory",
        "class": "directory",
    }
    assert document["status"]["state"] == "ready"


def test_configured_path_variants_are_published_exactly_and_do_not_define_identity(tmp_path):
    family = tmp_path / "family"
    family.mkdir()
    (family / "Model-Q4_K_M.gguf").write_bytes(b"gguf")
    root = DirectoryRoot(
        "models",
        tmp_path,
        "Models",
        (
            PathVariantTemplate("Runtime", "/srv/models/{rel}"),
            PathVariantTemplate("Operator note", r"share://models?artifact={rel}&mode=copy"),
        ),
    )

    artifacts, _ = scan_directory_root(root)

    assert artifacts[0]["path_variants"] == [
        {"label": "Runtime", "value": "/srv/models/family/Model-Q4_K_M.gguf"},
        {
            "label": "Operator note",
            "value": "share://models?artifact=family/Model-Q4_K_M.gguf&mode=copy",
        },
    ]
    assert "/srv/models" not in artifacts[0]["id"]


def test_actual_path_variant_preserves_temporary_filename(tmp_path):
    (tmp_path / "Model-Q4_K_M.gguf.incomplete").write_bytes(b"partial")
    root = DirectoryRoot(
        "models",
        tmp_path,
        "Models",
        (PathVariantTemplate("Observed", "{path}"),),
    )

    artifacts, _ = scan_directory_root(root)

    assert artifacts[0]["path_variants"][0]["value"].endswith(
        "Model-Q4_K_M.gguf.incomplete"
    )


def test_unreachable_root_is_reported_without_raising(tmp_path):
    missing = tmp_path / "missing"

    document = inventory_document([DirectoryRoot("models", missing, "Offline store")])

    assert document["artifacts"] == []
    assert document["status"]["state"] == "unreachable"
    assert document["status"]["sources"][0]["state"] == "unreachable"


@pytest.mark.parametrize("root_id", ["", "spaces are not stable", ":bad", "a" * 65])
def test_root_ids_are_restricted(root_id, tmp_path):
    with pytest.raises(ValueError):
        DirectoryRoot(root_id, Path(tmp_path), "Models")


def test_live_cli_requires_explicit_provider_authority(tmp_path, capsys):
    from artifact_store.directory_provider import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--root", f"models={tmp_path}"])

    assert exc_info.value.code == 2
    assert "--provider-id" in capsys.readouterr().err
