"""Regression guard for #5558 — POST /api/personal/add_directory must not run
the indexing job on the event loop.

The handler is ``async def`` but called ``rag.index_personal_documents``
(os.walk + file reads + per-chunk embedding + Chroma inserts) inline, so
FastAPI ran the whole job on the event loop and every other request queued
behind it: indexing a real directory froze the UI and API for 25+ minutes.
``personal_docs_manager.add_directory`` sits in the same blocking section — it
triggers ``refresh_index()``, which re-extracts text across tracked dirs.

These tests build the real router with fake managers and compare the thread
the indexing work runs on against the event loop's thread.
"""
import os
import threading

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.personal_routes as personal_routes
from core.middleware import require_admin
from src.auth_helpers import require_user


class _FakeRag:
    def __init__(self, record):
        self._record = record

    def index_personal_documents(self, directory, owner=None):
        self._record["index_thread"] = threading.get_ident()
        return {"success": True, "indexed_count": 3, "failed_count": 0}


class _FakeDocsManager:
    def __init__(self, record):
        self._record = record
        self.index = []

    def add_directory(self, directory, *, index=True, owner=None):
        self._record["bookkeeping_thread"] = threading.get_ident()
        self._record["bookkeeping_index_flag"] = index


def _build_app(tmp_path, monkeypatch, record):
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: _FakeRag(record))

    app = FastAPI()
    app.include_router(
        personal_routes.setup_personal_routes(_FakeDocsManager(record), None, True)
    )
    app.dependency_overrides[require_user] = lambda: "tester"
    app.dependency_overrides[require_admin] = lambda: None

    @app.get("/loop-thread")
    async def loop_thread_probe():
        return {"thread": threading.get_ident()}

    return app


def test_indexing_runs_off_the_event_loop(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    # Context-manager client: one portal/event loop serves both requests, so
    # the probe and the POST are guaranteed to see the same loop thread.
    with TestClient(app) as client:
        loop_thread = client.get("/loop-thread").json()["thread"]
        resp = client.post(
            "/api/personal/add_directory", json={"directory": str(target)}
        )

    assert resp.status_code == 200
    assert record["index_thread"] != loop_thread, (
        "index_personal_documents ran on the event loop thread — every other "
        "request queues behind the indexing job (#5558)"
    )
    assert record["bookkeeping_thread"] != loop_thread, (
        "personal_docs_manager.add_directory (refresh_index) ran on the event "
        "loop thread"
    )


def test_response_and_bookkeeping_unchanged(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    client = TestClient(app)
    resp = client.post("/api/personal/add_directory", json={"directory": str(target)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["indexed_count"] == 3
    assert body["failed_count"] == 0
    assert body["directory"] == os.path.realpath(str(target))
    assert record["bookkeeping_index_flag"] is False


def test_concurrent_add_directory_requests_serialize_indexing(tmp_path, monkeypatch):
    """Off-loop execution must not mean parallel index jobs: concurrent
    requests would race PersonalDocsManager's unsynchronized list mutations
    and file writes (save_directories/_save_excluded are plain open('w'))."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    record = {}
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def _slow_index(self, directory, owner=None):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.2)
        with state_lock:
            state["active"] -= 1
        return {"success": True, "indexed_count": 1, "failed_count": 0}

    monkeypatch.setattr(_FakeRag, "index_personal_documents", _slow_index)

    app = _build_app(tmp_path, monkeypatch, record)
    for name in ("docs_a", "docs_b"):
        (tmp_path / name).mkdir()

    client = TestClient(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                client.post,
                "/api/personal/add_directory",
                json={"directory": str(tmp_path / name)},
            )
            for name in ("docs_a", "docs_b")
        ]
        results = [f.result() for f in futures]

    assert all(r.status_code == 200 for r in results)
    assert state["max_active"] == 1, (
        f"{state['max_active']} index jobs ran in parallel — concurrent "
        "add_directory requests must serialize"
    )


def test_failed_indexing_still_returns_500(tmp_path, monkeypatch):
    record = {}
    app = _build_app(tmp_path, monkeypatch, record)
    target = tmp_path / "docs"
    target.mkdir()

    def _fail(directory, owner=None):
        return {"success": False, "message": "boom"}

    monkeypatch.setattr(_FakeRag, "index_personal_documents", staticmethod(_fail))

    client = TestClient(app)
    resp = client.post("/api/personal/add_directory", json={"directory": str(target)})
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]
