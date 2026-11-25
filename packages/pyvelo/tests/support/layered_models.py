"""Synthetic ``Layered`` models shaped like the firewall and QoS modules, for the tests of the layer machinery.

``tests/support`` is on pytest's ``pythonpath`` and basedpyright's
``extraPaths``, so the tests import this as ``layered_models``. It imports
``pyvelo`` only.
"""

from typing import Annotated, ClassVar

from pydantic import MISSING, Field
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.core import (
    Additive,
    Authority,
    ByFlag,
    ByPresence,
    Default,
    EdgeOnly,
    Inheritable,
    KeyField,
    Layered,
    MemberOf,
    OwnedGroup,
    ProfileOnly,
    ServerOwned,
    Unconverted,
    VcoModel,
    kind_default,
)
from pyvelo.core.layers import L


def profile_false_wins(profile_value: object) -> bool:
    """The firewall's authority rule: a profile that has the setting off turns it off for every edge."""
    return profile_value is False


class Settings(VcoModel):
    a: int = 1
    b: bool = False


class Flagged(VcoModel):
    override: bool = False
    value: int = 0


class Reset(VcoModel):
    override: bool = False
    affinity: list[str] = Field(default_factory=list)


class PairLocal(VcoModel):
    """What the group ``pair`` stores when it is local: every member, all required."""

    pair_a: Settings
    pair_b: Settings
    pair_on: bool


class Rule(VcoModel):
    name: str
    action: str = "allow"
    rule_logical_id: Annotated[str | MISSING, ServerOwned()] = MISSING


class Seg(Layered[L]):
    segment: SegmentMetadata
    rules: Annotated[Additive[list[Rule]], KeyField("name", alt="rule_logical_id")] = (
        kind_default(factory=list)
    )
    mapping: Annotated[
        Inheritable[Flagged],
        ByFlag("override", presence_wins=True),
        Default(factory=Flagged),
    ] = Field(default=...)
    legacy: Unconverted[int | None] = kind_default(None)


class Mod(Layered[L]):
    segments: Annotated[
        list[Seg[L]], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)
    enabled: Annotated[
        Inheritable[bool], ByPresence(), Default(True), Authority(profile_false_wins)
    ] = Field(default=...)
    level: Annotated[Inheritable[int | None], ByPresence(), Default(None)] = Field(
        default=...
    )
    settings: Annotated[
        Inheritable[Settings], ByPresence(), Default(factory=Settings)
    ] = Field(default=...)
    pair_a: Annotated[
        Inheritable[Settings], ByPresence(), MemberOf("pair"), Default(factory=Settings)
    ] = Field(default=...)
    pair_b: Annotated[
        Inheritable[Settings], ByPresence(), MemberOf("pair"), Default(factory=Settings)
    ] = Field(default=...)
    pair_on: Annotated[
        Inheritable[bool], ByPresence(), MemberOf("pair"), Default(False)
    ] = Field(default=...)
    pair: ClassVar[OwnedGroup] = OwnedGroup("pair", anchor="pair_a", bundle=PairLocal)
    affinity: Annotated[
        Inheritable[Reset],
        ByFlag("override", inherit_as=lambda: Reset(override=False)),
        Default(factory=Reset),
    ] = Field(default=...)
    edge_note: EdgeOnly[str] = kind_default("")
    profile_note: ProfileOnly[str] = kind_default("")
    legacy: Unconverted[list[int]] = kind_default(factory=list)


def mod_json(**keys: object) -> dict[str, object]:
    """A wire dict for ``Mod``: no segments, and *keys* (wire keys, as the VCO writes them)."""
    return {"segments": [], **keys}
