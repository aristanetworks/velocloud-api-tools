"""``Owned``: what an inheritable field holds on the edge layer, and the encodings that say how it is stored.

An edge layer may override a value its profile layer provides. ``Owned[T]``
keeps what the edge stores at the key, and whether that counts as an override
follows from the field's encoding, declared once on the field.

This module holds the data: ``Owned`` with what it stores, and the two
encodings as plain values. The ownership intents (``set_local``, ``inherit``,
``localize``) and the way an encoding reads a stored value are not here yet.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import MISSING


@dataclass(frozen=True)
class ByPresence:
    """Encoding: a value is local when its key is present and not ``null``.

    ``false``, ``0``, ``{}`` and ``[]`` count as present.
    """


@dataclass(frozen=True)
class ByFlag:
    """Encoding: an object is local when it is present and its flag *flag* is true.

    *inherit_as* makes the object a path that persists a reset keeps (for
    instance ``{override: false, affinity: []}``); without it, inheriting
    omits the object. *presence_wins* records that the backend composer
    selects a present edge object without checking the flag.
    """

    flag: str
    inherit_as: Callable[[], object] | None = None
    presence_wins: bool = False


type Encoding = ByPresence | ByFlag


class Owned[T]:
    """What an ``Inheritable[T]`` field holds on the edge layer.

    *stored* is what the edge layer holds at the key, parsed: a value,
    ``None`` for a stored JSON ``null``, or ``MISSING`` when the key is
    absent. *is_local* says whether that counts as the edge overriding the
    profile; *local* is *stored* then, and ``MISSING`` otherwise. An
    ``Owned`` that belongs to no model reads *is_local* by presence.
    """

    stored: T | None | MISSING

    def __init__(self, stored: T | None | MISSING = MISSING) -> None:
        self.stored = stored

    @property
    def is_local(self) -> bool:
        return self.stored is not MISSING and self.stored is not None

    @property
    def local(self) -> T | MISSING:
        stored = self.stored
        if stored is MISSING or stored is None:
            return MISSING
        return stored

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Owned) and self.stored == other.stored  # pyright: ignore[reportUnknownMemberType]

    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __repr__(self) -> str:
        return f"Owned({self.stored!r})"
