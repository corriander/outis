"""Regression guard for the README title presentation.

Originally (#1390) the README opened with an ASCII-art banner that had to live
inside a ``` code fence, otherwise GitHub's markdown collapsed its leading
whitespace and box-drawing rules and rendered it misaligned. The README refresh
(#4306) dropped that banner in favour of a centered wordmark image, so the guard
now pins the wordmark identity instead, while still catching the original failure
mode if an un-fenced ASCII banner is ever reintroduced.
"""
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

# Box-drawing rule from the legacy ASCII banner (the #1390 failure mode).
_RULE = "─" * 10


def _fenced_segments(text: str):
    """Return the segments of *text* that sit INSIDE ``` fences."""
    parts = text.split("```")
    # parts[0] is before the first fence, parts[1] is inside the first fence, ...
    return parts[1::2]


def test_readme_opens_with_project_title():
    # The README must open with a recognizable project title. Upstream pins its
    # wordmark image here; this fork's README opens with a plain H1 instead.
    first_line = README.read_text(encoding="utf-8").lstrip().splitlines()[0]
    assert first_line.rstrip() == "# Outis", "README must open with the '# Outis' title"


def test_reintroduced_ascii_banner_stays_fenced():
    # Defensive: if a box-drawing banner is ever added back, it must be fenced so
    # GitHub renders it monospace-as-typed (the original #1390 regression).
    text = README.read_text(encoding="utf-8")
    if _RULE not in text:
        return
    inside = "\n".join(_fenced_segments(text))
    assert _RULE in inside, "ASCII banner rule must be inside a ``` code fence"
