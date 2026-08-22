"""Display-time scrubbing must leave code alone.

``stripToolBlocks`` in static/js/chatRenderer.js runs over the whole message
before markdown parsing. Most of its patterns are unambiguous machine syntax
(``[TOOL_CALL]``, DSML markup, raw OpenAI tool JSON) and are safe to remove
wherever they appear. Three are word-shaped:

* ``TOOL_NARRATION_RE`` deletes any line matching ``stdout:``/``stderr:``/
  ``exit_code:`` — which is the model narrating a tool result in prose, and a
  perfectly ordinary line in a pasted log, a YAML sample, or a CI config.
* ``QWEN_ROLE_MARKER_RE`` / ``QWEN_BARE_MARKER_RE`` remove leaked chat-template
  turn markers — which are content, not noise, in a code block about chat
  templates.

Those three now run through ``mapOutsideCode``, so fenced blocks and inline
code spans come out byte-identical to what the model sent.

chatRenderer.js pulls in browser globals and can't be imported under node, so
the behavioural half exercises the real helper module plus the regex literals
scraped from chatRenderer.js — the same approach as
tests/test_tool_parsing_bare_end_marker.py.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHAT_RENDERER = _REPO / "static" / "js" / "chatRenderer.js"
_OUTSIDE_CODE = _REPO / "static" / "js" / "markdown" / "outsideCode.js"
_HAS_NODE = shutil.which("node") is not None

F = "```"


def _regex_literal(name: str) -> str:
    src = _CHAT_RENDERER.read_text(encoding="utf-8")
    m = re.search(rf"^const {name} =\s*(/.*/[gimsuy]*);$", src, re.MULTILINE)
    assert m, f"{name} literal not found in chatRenderer.js"
    return m.group(1)


def _scrub(cases: list[str]) -> list[str]:
    """Run the word-shaped passes exactly as stripToolBlocks composes them."""
    script = """
        import { mapOutsideCode } from 'HELPER';
        const ROLE = %s, BARE = %s, NARRATION = %s;
        const cases = JSON.parse(process.argv[1]);
        const out = cases.map(text => mapOutsideCode(text, chunk => chunk
          .replace(ROLE, '')
          .replace(BARE, ' ')
          .replace(NARRATION, '')));
        console.log(JSON.stringify(out));
    """.replace("HELPER", _OUTSIDE_CODE.as_uri()) % (
        _regex_literal("QWEN_ROLE_MARKER_RE"),
        _regex_literal("QWEN_BARE_MARKER_RE"),
        _regex_literal("TOOL_NARRATION_RE"),
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(cases)],
        cwd=str(_REPO), capture_output=True, timeout=30, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_narration_lines_survive_inside_a_fence():
    fenced = f"Here's the run:\n{F}\nstdout: hello world\nexit_code: 0\n{F}\nAll good."
    assert _scrub([fenced])[0] == fenced


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_narration_lines_are_still_stripped_in_prose():
    out = _scrub(["The output shows: stdout: hello world"])[0]
    assert "hello world" not in out


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_inline_code_span_is_left_alone():
    text = "grep for `exit_code: 1` in the log"
    assert _scrub([text])[0] == text


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_a_chat_template_sample_keeps_its_markers():
    sample = f"{F}jinja\n<|assistant|>\n{{{{ content }}}}</|end|>\n{F}"
    assert _scrub([sample])[0] == sample


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_leaked_markers_outside_code_are_still_stripped():
    out = _scrub(["<|assistant|>Hello there"])[0]
    assert out == "Hello there"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_an_unclosed_fence_protects_the_streaming_tail():
    """Mid-stream, the tail of a fence that has not closed yet is still code."""
    partial = f"Running it now:\n{F}\nstdout: partial output so f"
    assert _scrub([partial])[0] == partial


def test_stripToolBlocks_routes_the_word_shaped_passes_through_map_outside_code():
    src = _CHAT_RENDERER.read_text(encoding="utf-8")
    body = src[src.index("export function stripToolBlocks("):]
    body = body[: body.index("\n}\n")]
    assert "mapOutsideCode(cleaned" in body
    for name in ("QWEN_ROLE_MARKER_RE", "QWEN_BARE_MARKER_RE", "TOOL_NARRATION_RE"):
        assert f"cleaned = cleaned.replace({name}" not in body, (
            f"{name} must not be applied to the whole message; it matches code too"
        )
