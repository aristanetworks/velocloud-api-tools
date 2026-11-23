"""How a plan line and a write error show a value.

``value_text`` is the one place a value becomes text. ``shown`` and
``shown_together`` cut that text to ``VALUE_WIDTH`` characters; the values of one
line are cut where they first differ, so a difference is never cut away.
"""

import json
import os
from collections.abc import Sequence
from typing import Final

from pydantic import MISSING

from pyvelo.core.rebase import ItemMove

VALUE_WIDTH: Final = 80
"""How many characters of a value's text a line shows."""

LEAD: Final = 20
"""How many characters before the first difference a cut value keeps."""

MOVES_SHOWN: Final = 5
"""How many moves of a reorder a line names; the rest are counted."""


def value_text(value: object) -> str:
    """*value* as text: its JSON with sorted keys, ``(nothing)`` for ``MISSING``.

    A value with no JSON (keys of mixed types, a value whose text cannot be
    made, a cycle) is shown by its ``repr``, and by its type name when that
    raises too. Never raises, and never cuts.
    """
    if value is MISSING:
        return "(nothing)"
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - a message must print whatever it holds
        try:
            return repr(value)
        except Exception:  # noqa: BLE001 - a message must print whatever it holds
            return f"<{type(value).__name__}>"


def shown(value: object) -> str:
    """``value_text`` of *value*, cut to ``VALUE_WIDTH`` characters, with ``…`` after it when text is left out."""
    text = value_text(value)
    return text if len(text) <= VALUE_WIDTH else f"{text[:VALUE_WIDTH]}…"


def shown_together(*values: object) -> tuple[str, ...]:
    """The values of one line as text, each cut at the same place.

    Texts of at most ``VALUE_WIDTH`` characters are whole. Otherwise each long
    text starts ``LEAD`` characters before the first character where the texts
    differ (at the start when they do not differ), and is ``VALUE_WIDTH``
    characters long; ``…`` marks each end where text is left out.
    """
    texts = [value_text(value) for value in values]
    if all(len(text) <= VALUE_WIDTH for text in texts):
        return tuple(texts)
    start = 0
    if any(text != texts[0] for text in texts):
        start = max(0, len(os.path.commonprefix(texts)) - LEAD)
    cut: list[str] = []
    for text in texts:
        if len(text) <= VALUE_WIDTH:
            cut.append(text)
            continue
        end = start + VALUE_WIDTH
        cut.append(
            ("…" if start > 0 else "")
            + text[start:end]
            + ("…" if end < len(text) else "")
        )
    return tuple(cut)


def moves_text(moves: Sequence[ItemMove]) -> str:
    """The first ``MOVES_SHOWN`` of *moves*, each with its new place, and a count of the rest."""
    text = "; ".join(str(move) for move in moves[:MOVES_SHOWN])
    if len(moves) > MOVES_SHOWN:
        text += f"; and {len(moves) - MOVES_SHOWN} more"
    return text
