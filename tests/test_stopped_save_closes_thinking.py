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


# ── a turn stopped while still thinking must still be saved ───────────────────


def test_thinking_only_partial_survives_the_save_path():
    """The shape a reasoning model produces when stopped mid-thought.

    Reasoning arrives on its own channel and is deliberately kept out of the
    saved reply, so a turn cancelled before any reply text has an empty
    `full_response`. The cancel handler used to save only when that was
    non-empty, so the entire turn vanished on reload. It now falls back to the
    thinking accumulator, wrapped as an unfinished block.
    """
    from routes.chat_helpers import clean_thinking_for_save

    thinking = "Let me work through the halfer case first. Wait — that assumes"
    content, md = clean_thinking_for_save("<think>" + thinking, {"stopped": True})

    assert content.startswith("<think>")
    assert content.endswith("</think>")
    assert thinking in content
    assert md["stopped"] is True

# -- where reasoning goes depends on how far the turn got ---------------------


def test_reasoning_cut_off_midway_rides_in_the_content():
    """Stopped while still thinking: no reply exists, so the block is the message."""
    from routes.chat_helpers import stopped_partial_for_save

    content, md = stopped_partial_for_save("", "Let me work through the halfer case", {})

    assert content.startswith("<think>")
    assert content.endswith("</think>")
    assert md["thinking_incomplete"] is True
    # The content carries the block here, so metadata must not carry it too --
    # the renderer prepends metadata.thinking and would draw two sections.
    assert "thinking" not in md


def test_completed_reasoning_survives_a_reply_cut_off_midway():
    """Stopped after thinking finished: the case that used to lose it entirely.

    Reasoning arrives on its own channel and never enters `full_response`, so
    this save kept the partial reply and dropped the reasoning on the floor.
    The thinking block was on screen while the stream ran and gone after a
    reload, while the same model's uninterrupted replies kept theirs.
    """
    from routes.chat_helpers import stopped_partial_for_save

    content, md = stopped_partial_for_save(
        "Little's Law relates the average", "Work through the queue case first", {}
    )

    assert content == "Little's Law relates the average"
    assert md["thinking"] == "Work through the queue case first"
    assert md["stopped"] is True
    # The reasoning completed. Only the reply was interrupted, so labelling the
    # thinking block "(interrupted)" would be a lie.
    assert "thinking_incomplete" not in md


def test_inline_reasoning_is_left_to_the_ordinary_extraction():
    """Models that emit <think> inside the reply need no special handling."""
    from routes.chat_helpers import stopped_partial_for_save

    content, md = stopped_partial_for_save("<think>done</think>Here you go.", "", {})

    assert content == "Here you go."
    assert md["thinking"] == "done"


def test_a_turn_that_generated_nothing_saves_nothing():
    from routes.chat_helpers import stopped_partial_for_save

    content, _ = stopped_partial_for_save("", "", {})

    assert not content


def test_both_cancel_handlers_route_through_the_helper():
    """Source-level guard: the routes are generators inside a closure and cannot
    be exercised directly.

    Agent mode is the reason this counts both. The fix originally landed on the
    chat-mode handler alone, so an agent turn stopped mid-thought still saved
    nothing and vanished on reload.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"
    body = src.read_text(encoding="utf-8")
    assert body.count("stopped_partial_for_save(") == 2
    # Neither handler may go back to saving full_response unconditionally.
    assert "if full_response and not incognito:" not in body
