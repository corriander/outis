"""Reasoning that was cut off stays reasoning.

``extractThinkingBlocks`` has to cope with an unclosed ``<think>``, which has
two very different causes:

(a) the model never closes its tags — some quantized builds emit a literal
    ``<think>`` at the start of every reply and never close it, so the body
    really is the reply, and
(b) the generation stopped early — cancelled, dropped, or out of tokens — so
    the body is reasoning that never finished.

The text cannot tell them apart, and the old heuristic always chose (a): a
cancelled turn had its reasoning promoted to the visible reply. Because the
bubble's text is also what gets stored and replayed, the model's private
reasoning then went back into context as something it had said out loud.

The callers know which case they are in. The renderer passes
``complete: false`` when the message is a cancelled one (live, or restored from
``metadata.stopped``), and the save path closes the tag at the point where the
stream was stopped, so nothing downstream has to guess at all.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


def _extract(text: str, options: dict | None = None) -> dict:
    """Run markdown.js extractThinkingBlocks(text, options) under node."""
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        globalThis.window = { location: { origin: 'http://localhost' }, katex: null };
        globalThis.document = {
          readyState: 'loading',
          addEventListener() {},
          createElement(tag) {
            if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
            return {
              _html: '',
              content: { querySelectorAll() { return []; } },
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
            };
          },
        };
        globalThis.MutationObserver = class { observe() {} };

        let source = fs.readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(/import uiModule from ['"]\.\/ui\.js['"];/, '');
        source = source.replace(
          /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
          `function splitTableRow(row) {
            return (row || '').replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
          }`
        );
        const emojiSource = fs.readFileSync('./static/js/emojiShortcodes.js', 'utf8')
          .replace(/^export default .*$/m, '')
          .replace(/export const /g, 'const ')
          .replace(/export function /g, 'function ');
        source = source.replace(
          /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
          () => emojiSource
        );
        source = source.replace(
          /var escapeHtml = uiModule\.esc;/,
          `var escapeHtml = (value) => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');`
        );

        const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
        const mod = await import(moduleUrl);
        const [input, options] = JSON.parse(process.argv[1]);
        console.log(JSON.stringify({ out: mod.extractThinkingBlocks(input, options) }));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps([text, options or {}])],
        cwd=_REPO, capture_output=True, timeout=20, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, f"node failed:\n{result.stderr}"
    return json.loads(result.stdout.splitlines()[-1])["out"]


CUT_OFF = "<think>The user wants a shell equivalent. First I need to work out how"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_interrupted_reasoning_is_not_promoted_to_the_reply():
    out = _extract(CUT_OFF, {"complete": False})
    assert out["content"] == ""
    assert out["thinkingBlocks"] == ["The user wants a shell equivalent. First I need to work out how"]
    assert out["incompleteThinking"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_a_finished_generation_still_treats_a_stray_opener_as_the_reply():
    """Case (a) is untouched: the quantized-model shape must keep working."""
    out = _extract(CUT_OFF)
    assert out["content"] == "The user wants a shell equivalent. First I need to work out how"
    assert out["thinkingBlocks"] == []
    assert out["incompleteThinking"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_interrupted_reasoning_after_a_reply_is_kept_not_dropped():
    """The other truncation shape: reply text, then reasoning that got cut."""
    out = _extract("Here you go.\n\n<think>Although, wait — the year field", {"complete": False})
    assert out["content"] == "Here you go."
    assert out["thinkingBlocks"] == ["Although, wait — the year field"]
    assert out["incompleteThinking"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_a_closed_block_is_unaffected_by_the_flag():
    for options in ({}, {"complete": False}):
        out = _extract("<think>all done</think>The answer is 42.", options)
        assert out["content"] == "The answer is 42."
        assert out["thinkingBlocks"] == ["all done"]
        assert out["incompleteThinking"] is False
