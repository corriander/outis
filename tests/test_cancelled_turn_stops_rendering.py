"""A delta arriving after Stop must not redraw the message.

Cancelling a turn writes the final render into `.body` and appends a stopped
indicator after it. The streaming renderer's `_ensureStreamLayout` only looks
for an *existing* `.stream-content` div, so a delta processed after that point
appends a fresh one below the indicator and draws the message a second time —
a duplicated reply when the reasoning had closed, or a stale "Thinking (N
lines)" bar when it had not. Both disappear on refresh, because the server
stored one copy; only the live bubble is wrong.

`_renderStream` is defined inside a closure several hundred lines into a
streaming handler and cannot be imported, so the guard is pinned at the source
level, as tests/test_stopped_save_closes_thinking.py does for the route.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = ROOT / "static" / "js" / "chat.js"


@pytest.fixture(scope="module")
def render_stream_guard() -> str:
    """The text between the start of _renderStream and its first rendering work."""
    parts = CHAT_JS.read_text(encoding="utf-8").split("_renderStream = () => {", 1)
    assert len(parts) == 2, "_renderStream is no longer an arrow function assignment"
    body = parts[1]
    return body[: body.index("let dt =")]


def test_render_stream_bails_once_the_stream_is_aborted(render_stream_guard):
    assert "abortCtrl" in render_stream_guard
    assert "signal.aborted" in render_stream_guard
    assert "return" in render_stream_guard


def test_the_guard_does_not_key_off_the_indicator_class(render_stream_guard):
    """`stopped-indicator` is reused for transient notices — stall, offline,
    timeout, rounds-exhausted — that appear while a stream is still running.
    Guarding on its presence would freeze the live render for the rest of the
    turn, so the abort signal is the only safe signal here."""
    assert "stopped-indicator" not in render_stream_guard


def test_every_chat_js_import_agrees_on_the_cache_bust():
    """A module imported under two different `?v=` tokens is fetched and
    evaluated twice, and the two copies share no state."""
    tokens = set()
    for path in (ROOT / "static").rglob("*"):
        if path.suffix not in {".html", ".js"} or not path.is_file():
            continue
        tokens.update(re.findall(r"chat\.js\?v=([A-Za-z0-9._-]+)", path.read_text(encoding="utf-8")))
    assert len(tokens) == 1, f"chat.js is imported under multiple tokens: {sorted(tokens)}"
