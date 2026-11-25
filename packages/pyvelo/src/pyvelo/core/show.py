"""How a plan line and a write error show a value.

``value_text`` is the one place a value becomes text. ``shown`` and
``shown_together`` cut that text to ``VALUE_WIDTH`` characters; the values of one
line are cut where they first differ, so a difference is never cut away. Given
the model of the document and the path of the values, they show ``***`` in place
of every secret first.
"""

import json
import os
from collections.abc import Sequence
from typing import Final

from pydantic import MISSING

from pyvelo.core.base import VcoModel
from pyvelo.core.rebase import ItemMove, Key, Path
from pyvelo.core.secrets import hide_secrets

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


def _hidden(value: object, model: type[VcoModel] | None, path: Path | None) -> object:
    """*value* with its secrets hidden when both *model* and *path* are given."""
    if model is None or path is None:
        return value
    return hide_secrets(model, path, value)


def shown(
    value: object,
    *,
    model: type[VcoModel] | None = None,
    path: Path | None = None,
) -> str:
    """``value_text`` of *value*, cut to ``VALUE_WIDTH`` characters.

    ``…`` follows it when text is left out.

    Given both *model* and *path* (where in a document of *model* the value
    is), a secret in the value is shown as ``"***"`` (``hide_secrets``).
    """
    text = value_text(_hidden(value, model, path))
    return text if len(text) <= VALUE_WIDTH else f"{text[:VALUE_WIDTH]}…"


def shown_together(
    *values: object,
    model: type[VcoModel] | None = None,
    path: Path | None = None,
) -> tuple[str, ...]:
    """The values of one line as text, each cut at the same place.

    Texts of at most ``VALUE_WIDTH`` characters are whole. Otherwise each long
    text starts ``LEAD`` characters before the first character where the texts
    differ (at the start when they do not differ), and is ``VALUE_WIDTH``
    characters long; ``…`` marks each end where text is left out. *model* and
    *path* hide secrets as they do for ``shown``, before the texts are cut.
    """
    texts = [value_text(_hidden(value, model, path)) for value in values]
    if all(len(text) <= VALUE_WIDTH for text in texts):
        return tuple(texts)
    start = 0
    if any(text != texts[0] for text in texts):
        start = max(0, len(os.path.commonprefix(texts)) - LEAD)  # noqa: RUF071  # a character prefix, not a path
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
    """The first ``MOVES_SHOWN`` of *moves*, each with its new place.

    With them comes a count of the rest.
    """
    text = "; ".join(str(move) for move in moves[:MOVES_SHOWN])
    if len(moves) > MOVES_SHOWN:
        text += f"; and {len(moves) - MOVES_SHOWN} more"
    return text


def patch_text(path: Path, *, clear: bool = False) -> str:
    """The merge patch that sets the field at *path* to a service, or clears it.

    It is JSON a text-only caller can send.

    The path is in the names a patch is written in; a keyed item is named by
    its key. The value is a placeholder to replace with the name or the
    logical ID of a service that exists, or ``null`` with *clear*.
    """
    patch: object = (
        None if clear else "<the name or the logical ID of a service that exists>"
    )
    for part in reversed(path.parts):
        text = str(part.value) if isinstance(part, Key) else str(part)
        patch = {text: patch}
    return json.dumps(patch)
