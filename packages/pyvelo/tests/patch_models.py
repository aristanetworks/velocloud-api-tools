"""Synthetic models shared by the patch tests."""

from typing import Annotated, Any

from pydantic import MISSING, Field
from pyvelo.core import (
    DefaultingModel,
    DefaultingScalar,
    KeyField,
    Positional,
    VcoModel,
)


class Sub(VcoModel):
    v: int = 0
    note: str | None = None
    opt: int | MISSING = MISSING


class Rule(VcoModel):
    name: str
    rule_id: str | MISSING = MISSING
    priority: int = 0
    sub: Sub | MISSING = MISSING
    tags: list[str] = Field(default_factory=list)


class Hop(VcoModel):
    ip: str
    cost: int = 0


class Route(VcoModel):
    dest: str
    prefix: int | str
    gw: str
    intf: str = ""
    cost: int = 0


class Link(VcoModel):
    internal_id: str | MISSING = MISSING
    family: str = "IPv4"
    name: str


class Root(VcoModel):
    flag: bool = False
    count: int = 0
    title: str | None = None
    sub: Sub = Field(default_factory=Sub)
    maybe: Sub | MISSING = MISSING
    need: Rule | MISSING = MISSING
    safe: Annotated[bool, DefaultingScalar(bool, default=False)] = False
    hops_safe: Annotated[
        list[Hop], DefaultingModel(list[Hop], default_factory=list)
    ] = Field(default_factory=list)
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)
    ports: list[int] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    hops: list[Hop] = Field(default_factory=list)
    blobs: list[dict[str, Any]] = Field(default_factory=list)
    by_name: dict[str, Sub] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    seq: Annotated[list[str], Positional()] = Field(default_factory=list)
