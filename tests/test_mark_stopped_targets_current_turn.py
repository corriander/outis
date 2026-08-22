"""A cancelled turn must not mark the *previous* reply as interrupted.

`POST /api/session/{id}/mark-stopped` marked the newest assistant message in the
session, with no check that it belonged to the turn just cancelled. Stop a
reasoning model while it is still thinking and the turn produces nothing the
server keeps — so the marker fell through onto the previous, completed reply,
which then rendered "[Message interrupted]" with a Continue button offering to
resume a message that had finished normally.

The rule: the newest assistant message belongs to this turn only if it comes
after the last user message.
"""
import pytest

from core.models import ChatMessage
from routes.history.history_routes import last_assistant_belongs_to_current_turn


def _user(text="hi"):
    return ChatMessage("user", text)


def _assistant(text="hello"):
    return ChatMessage("assistant", text)


def test_reply_from_this_turn_is_markable():
    history = [_user(), _assistant()]
    assert last_assistant_belongs_to_current_turn(history) is True


def test_reply_from_an_earlier_turn_is_not_markable():
    # The reported shape: hello -> reply -> prompt -> cancelled before any output.
    history = [_user("hello"), _assistant("hey"), _user("hard question")]
    assert last_assistant_belongs_to_current_turn(history) is False


def test_no_assistant_message_at_all():
    assert last_assistant_belongs_to_current_turn([_user()]) is False


def test_empty_history():
    assert last_assistant_belongs_to_current_turn([]) is False
    assert last_assistant_belongs_to_current_turn(None) is False


def test_plain_dict_history_is_handled():
    """Sessions restored from JSON carry dicts rather than ChatMessage."""
    assert last_assistant_belongs_to_current_turn(
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    ) is True
    assert last_assistant_belongs_to_current_turn(
        [{"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    ) is False


@pytest.mark.parametrize("junk", [None, {}, {"role": None}])
def test_malformed_entries_do_not_raise(junk):
    assert last_assistant_belongs_to_current_turn([junk]) is False


def test_route_consults_the_guard():
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "routes" / "history" / "history_routes.py"
    body = src.read_text(encoding="utf-8")
    marker = body.index("mark-stopped")
    window = body[marker : marker + 2000]
    assert "last_assistant_belongs_to_current_turn(session.history)" in window
