"""Synthetic ``Layered`` models shaped like the firewall and QoS modules, for the tests of the layer machinery.

``tests/support`` is on pytest's ``pythonpath`` and basedpyright's
``extraPaths``, so the tests import this as ``layered_models``. It imports
``pyvelo`` only.
"""

from typing import Annotated

from pydantic import MISSING, Field
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.core import (
    Additive,
    ByPresence,
    Default,
    EdgeOnly,
    Inheritable,
    KeyField,
    Layered,
    ProfileOnly,
    ServerOwned,
    Unconverted,
    VcoModel,
)
from pyvelo.core.layers import L


class Settings(VcoModel):
    a: int = 1
    b: bool = False


class Rule(VcoModel):
    name: str
    action: str = "allow"
    rule_logical_id: Annotated[str | MISSING, ServerOwned()] = MISSING


class Seg(Layered[L]):
    segment: SegmentMetadata
    rules: Annotated[Additive[list[Rule]], KeyField("name", alt="rule_logical_id")] = (
        Field(default_factory=list)  # pyright: ignore[reportAssignmentType]
    )
    legacy: Unconverted[int | None] = None  # pyright: ignore[reportAssignmentType]


class Mod(Layered[L]):
    segments: Annotated[
        list[Seg[L]], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)
    enabled: Annotated[Inheritable[bool], ByPresence(), Default(True)] = Field(
        default=...
    )
    level: Annotated[Inheritable[int | None], ByPresence(), Default(None)] = Field(
        default=...
    )
    settings: Annotated[
        Inheritable[Settings], ByPresence(), Default(factory=Settings)
    ] = Field(default=...)
    edge_note: EdgeOnly[str] = ""  # pyright: ignore[reportAssignmentType]
    profile_note: ProfileOnly[str] = ""  # pyright: ignore[reportAssignmentType]
    legacy: Unconverted[list[int]] = Field(default_factory=list)  # pyright: ignore[reportAssignmentType]


def mod_json(**keys: object) -> dict[str, object]:
    """A wire dict for ``Mod``: no segments, and *keys* (wire keys, as the VCO writes them)."""
    return {"segments": [], **keys}
