import json
from pathlib import Path

import pytest

from artifact_store.directory_provider import DirectoryRoot, inventory_document, scan_directory_root


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
    assert nested["display_location"] == "Local models / family / Nested-Q8_0.GGUF"
    assert nested["group_path"] == ["family"]
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


def test_artifact_id_is_stable_for_the_same_provider_path(tmp_path):
    path = tmp_path / "family" / "Model-Q5_K_M.gguf"
    path.parent.mkdir()
    path.write_bytes(b"first")
    first, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "First label"))
    path.write_bytes(b"different contents")
    second, _ = scan_directory_root(DirectoryRoot("models", tmp_path, "Renamed label"))

    assert first[0]["id"] == second[0]["id"]
    assert first[0]["display_location"] != second[0]["display_location"]


def test_inventory_never_exposes_provider_scan_root(tmp_path):
    (tmp_path / "Model-Q4_K_M.gguf").write_bytes(b"gguf")

    document = inventory_document([DirectoryRoot("models", tmp_path, "Local models")])
    encoded = json.dumps(document)

    assert str(tmp_path) not in encoded
    assert document["provider"] == {"id": "directory", "name": "Directory inventory"}
    assert document["status"]["state"] == "ready"


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
