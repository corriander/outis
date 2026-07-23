"""Outis attribution and corresponding-source offer regressions."""

from pathlib import Path

from src.constants import APP_VERSION, DEFAULT_OUTIS_SOURCE_URL, outis_build_ref, outis_source_url
from src.project_info import project_metadata


ROOT = Path(__file__).resolve().parents[1]


def test_source_url_defaults_to_public_outis_repository(monkeypatch):
    monkeypatch.delenv("OUTIS_SOURCE_URL", raising=False)

    assert outis_source_url() == DEFAULT_OUTIS_SOURCE_URL


def test_source_url_accepts_public_http_source(monkeypatch):
    source = "https://code.example/outis/tree/release-1"
    monkeypatch.setenv("OUTIS_SOURCE_URL", source)

    assert outis_source_url() == source


def test_source_url_rejects_executable_or_credentialed_urls(monkeypatch):
    for unsafe in (
        "javascript:alert(1)",
        "file:///srv/private/outis",
        "https://token@example.test/outis",
        "https://[invalid",
        "https://example.test\\@redirect.invalid/outis",
        "/relative/source",
    ):
        monkeypatch.setenv("OUTIS_SOURCE_URL", unsafe)
        assert outis_source_url() == DEFAULT_OUTIS_SOURCE_URL


def test_project_metadata_includes_build_provenance(monkeypatch):
    monkeypatch.setenv("OUTIS_SOURCE_URL", "https://code.example/outis/tree/abc123")
    monkeypatch.setenv("OUTIS_BUILD_REF", "  abc123  ")

    metadata = project_metadata()

    assert metadata == {
        "version": APP_VERSION,
        "project": "Outis",
        "upstream": "Odysseus",
        "license": "AGPL-3.0-or-later",
        "license_url": "https://www.gnu.org/licenses/agpl-3.0.html",
        "source_url": "https://code.example/outis/tree/abc123",
        "build_ref": "abc123",
    }
    assert outis_build_ref() == "abc123"


def test_source_offer_is_visible_on_login_and_authenticated_shell():
    login = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="version-label"' in login
    assert "vd.source_url" in login
    assert "footer.textContent = 'v' + vd.version" in login
    assert 'id="outis-source-offer"' in index
    assert "d.source_url" in index
    assert "noopener noreferrer" in login
    assert "noopener noreferrer" in index
