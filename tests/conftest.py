"""Shared test configuration - ensure project root is on sys.path and stub heavy deps."""
import sys
import os
import types
import importlib.util
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing core.database below runs init_db() at import time, and its default
# (sqlite:///./data/app.db) can't be opened in a clean worktree because SQLite
# won't create the missing ./data parent dir - pytest then dies during
# collection, before any test module loads. Default to an in-memory DB for the
# test session so collection is deterministic and writes no repo-local
# artifacts. An explicit DATABASE_URL (a real test/CI database) is preserved.
# This only unblocks collection/import-time init; it does not provide a shared
# file-backed DB across processes - tests needing that must set DATABASE_URL.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Pin the suite to native mode explicitly (also the production default) so
# inherited Cookbook tests are insulated from ambient env; capability-boundary
# tests override this when asserting external-mode behaviour.
os.environ.setdefault("OUTIS_COOKBOOK_MODE", "native")

# Pre-import real heavy modules BEFORE any test file's module-level stubs can
# replace them with MagicMock. Some test files (e.g. test_llm_core_sanitize_*)
# stub sqlalchemy/core.database at module scope with `if mod not in sys.modules`,
# which fires during collection. If the real module hasn't been imported yet,
# the stub wins and contaminates every subsequent test that needs the real ORM.
try:
    import sqlalchemy  # noqa: F401
    import sqlalchemy.orm  # noqa: F401
    import core.database  # noqa: F401
    import src.database
except ImportError:
    pass  # not installed - the stubs below will handle it

def _has_module(mod_name: str) -> bool:
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ImportError, ValueError):
        return False


# Stub optional dependencies only when they are not installed. Do not replace
# real FastAPI/Starlette/Pydantic modules: route tests import their subpackages.
for mod_name in [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.types", "sqlalchemy.ext", "sqlalchemy.ext.declarative",
    "sqlalchemy.ext.hybrid", "sqlalchemy.sql", "sqlalchemy.sql.expression",
    "sqlalchemy.sql.sqltypes", "bcrypt", "pyotp",
    "httpx", "fastapi", "fastapi.responses", "fastapi.routing",
    "starlette", "starlette.responses", "starlette.middleware", "starlette.middleware.base",
    "pydantic",
]:
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()

if "src.database" not in sys.modules:
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["src.database"] = _db

# Pre-import core.models before test_agent_loop.py's module-level stubs
# run (it replaces sys.modules['core.models'] with a MagicMock during
# collection, which breaks session import in subsequent tests).
import core.models  # noqa: E402

import pytest  # noqa: E402

@pytest.fixture(autouse=True)
def _isolate_managed_state(tmp_path):
    """Point managed bootstrap state at a per-test directory.

    These modules resolve their paths from module-level names under ``data/``,
    so a test that exercises them writes into the developer's real data
    directory unless it patches every one. Worse, a leaked document is picked
    up by *later* tests -- a persisted provider config makes unrelated
    "unconfigured" assertions fail, and the failure surfaces far from the test
    that caused it.

    Redirecting them by default makes that impossible. Tests that need
    specific paths still patch these attributes themselves; being set up first,
    this fixture is torn down last, so those patches are unwound before the
    originals are restored here.

    Deliberately does not request ``monkeypatch``: an autouse conftest fixture
    that does would pull monkeypatch's setup earlier than every module-level
    autouse fixture, and so its teardown later than theirs. Modules that reload
    env-dependent code on teardown then see env vars a test set but monkeypatch
    has not yet restored.
    """
    import artifact_store.config as artifact_store_config
    import profile_service.config as profile_service_config
    import src.managed_transaction as managed_transaction

    active = tmp_path / "managed_state"
    active.mkdir()
    overrides = (
        (artifact_store_config, "ARTIFACT_STORE_CONFIG_FILE", "artifact_store.json"),
        (
            artifact_store_config,
            "ARTIFACT_STORE_CANDIDATE_FILE",
            "artifact_store.pending.json",
        ),
        (profile_service_config, "PROFILE_SERVICE_CONFIG_FILE", "profile_service.json"),
        (
            profile_service_config,
            "PROFILE_SERVICE_CANDIDATE_FILE",
            "profile_service.pending.json",
        ),
        (managed_transaction, "MANAGED_BOOTSTRAP_FILE", "managed_bootstrap.json"),
        (managed_transaction, "ARTIFACT_STORE_CONFIG_FILE", "artifact_store.json"),
    )

    original = [(module, name, getattr(module, name)) for module, name, _ in overrides]
    for module, name, filename in overrides:
        setattr(module, name, str(active / filename))
    try:
        yield active
    finally:
        for module, name, value in original:
            setattr(module, name, value)


def pytest_configure(config):
    """Register the dynamic taxonomy ``sub_*`` markers before collection.

    The stable ``area_*`` markers are declared in ``pyproject.toml``. The
    per-file ``sub_*`` markers are derived from the test filenames here so that
    unknown-mark warnings still surface genuine typos outside the taxonomy. This
    only registers marker names; it imports no production module.
    """
    import pathlib
    from tests._taxonomy import discover_markers

    tests_dir = pathlib.Path(__file__).parent
    paths = list(tests_dir.rglob("test_*.py")) + list(tests_dir.rglob("*_test.py"))
    for marker_name in discover_markers(paths):
        if marker_name.startswith("sub_"):
            config.addinivalue_line("markers", f"{marker_name}: taxonomy sub-area marker")


def pytest_collection_modifyitems(config, items):
    """Tag each collected test with its taxonomy ``area_*`` and ``sub_*`` markers.

    Collection-time only: this adds markers and nothing else. It does not skip,
    reorder, or deselect tests, mutate fixtures or the environment, or import any
    production module. See ``tests/_taxonomy.py`` for the classification rules.
    """
    import pytest
    from tests._taxonomy import markers_for_path

    for item in items:
        path = getattr(item, "path", None) or item.fspath
        for marker_name in markers_for_path(path):
            item.add_marker(getattr(pytest.mark, marker_name))
