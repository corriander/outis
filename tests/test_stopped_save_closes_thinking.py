"""A stopped save closes the reasoning block it interrupted.

A cancelled turn leaves `<think>` with no `</think>`, and every reader
downstream then has to guess whether that is reasoning cut off or a model that
never closes its tags — a question the text cannot answer but the code that
stopped the stream can. `clean_thinking_for_save` is that code: it is the save
path for a client that disconnected mid-stream, and it is handed
`{"stopped": True}`. Closing the tag there records the answer once, and the
ordinary extraction path then works on the partial message like any other.

See tests/test_interrupted_thinking_js.py for the renderer's half, which covers
the same message when it is still on screen.
"""
from src.text_helpers import close_unclosed_think


def test_close_unclosed_think_balances_an_interrupted_block():
    assert close_unclosed_think("<think>cut off").endswith("</think>")
    assert close_unclosed_think("<thinking>cut off").endswith("</think>")


def test_close_unclosed_think_leaves_balanced_text_alone():
    for text in ("<think>done</think>reply", "no tags at all", ""):
        assert close_unclosed_think(text) == text


def test_stopped_save_closes_the_block():
    from routes.chat_helpers import clean_thinking_for_save

    content, md = clean_thinking_for_save("<think>cut off", {"stopped": True})
    assert "</think>" in content or md.get("thinking") == "cut off"


def test_stopped_save_moves_thinking_to_metadata_when_a_reply_exists():
    from routes.chat_helpers import clean_thinking_for_save

    content, md = clean_thinking_for_save("<think>done</think>Here you go.", {"stopped": True})
    assert content == "Here you go."
    assert md["thinking"] == "done"


def test_an_ordinary_save_does_not_close_anything():
    """Only a save that knows the generation was stopped may add the tag."""
    from routes.chat_helpers import clean_thinking_for_save

    content, _ = clean_thinking_for_save("<think>still going", {})
    assert content == "<think>still going"
