"""``dataset.raw`` must mean the same thing on every render path.

Every message bubble records its markdown in ``dataset.raw``, and copy, edit,
resend, regenerate, variant capture and prompt recall all read it. The reload
path (``addMessage``) stored the display source — tool markup stripped — while
the cancel path and the multi-round agent path stored the unstripped stream.
The same reply therefore copied differently before and after a refresh, and
editing a cancelled one opened a textarea full of tool JSON.

``displaySource()`` is now the single definition of that value and every path
goes through it. chatRenderer.js and chat.js pull in browser globals and can't
be imported under node, so this pins the contract at the source level (same
approach as tests/test_resend_message_nondestructive.py).
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CHAT_RENDERER = _REPO / "static" / "js" / "chatRenderer.js"
_CHAT_JS = _REPO / "static" / "js" / "chat.js"


def test_display_source_is_the_one_definition():
    src = _CHAT_RENDERER.read_text(encoding="utf-8")
    body = src[src.index("export function displaySource("):]
    body = body[: body.index("\n}\n")]
    assert "stripToolBlocks(" in body
    assert "squashOutsideCode(" in body


def test_display_source_is_on_the_module_object():
    """chat.js reaches it as chatRenderer.displaySource."""
    src = _CHAT_RENDERER.read_text(encoding="utf-8")
    export_obj = src[src.index("const chatRenderer = {"):]
    export_obj = export_obj[: export_obj.index("};")]
    assert "displaySource," in export_obj


def test_add_message_derives_its_text_from_display_source():
    src = _CHAT_RENDERER.read_text(encoding="utf-8")
    assert "let text = displaySource(textRaw);" in src
    assert "squashOutsideCode(stripToolBlocks(textRaw" not in src, (
        "addMessage must not re-implement the display-source pipeline"
    )


def test_multi_round_agent_path_uses_display_source():
    src = _CHAT_RENDERER.read_text(encoding="utf-8")
    assert "resolveDocumentPlaceholderLinks(displaySource(roundTexts[r]), metadata)" in src


def test_cancelled_reply_stores_the_same_thing_a_finished_one_does():
    src = _CHAT_JS.read_text(encoding="utf-8")
    assert "const stoppedContent = chatRenderer.displaySource(currentAccumulated);" in src
    assert "const stoppedContent = currentAccumulated;" not in src, (
        "the cancel path must not keep the unstripped stream as dataset.raw"
    )
