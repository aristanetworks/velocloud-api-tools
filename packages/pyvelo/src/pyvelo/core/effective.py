"""The effective view: each value with where it comes from.

Only the data that the static types of a field name is here: ``Origin`` and
``Resolved``.
"""

from dataclasses import dataclass
from enum import StrEnum


class Origin(StrEnum):
    """Where an effective value comes from."""

    EDGE = "edge"
    PROFILE = "profile"
    FACTORY = "factory"


@dataclass(frozen=True)
class Resolved[T]:
    """An effective value with its *origin* and its *source*, the wire path in the layer it came from."""

    origin: Origin
    value: T
    source: str
