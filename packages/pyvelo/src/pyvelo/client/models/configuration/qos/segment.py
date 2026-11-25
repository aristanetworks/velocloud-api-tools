from typing import Annotated, Any

from pydantic import MISSING, Field

from pyvelo.core import (
    Additive,
    ByFlag,
    Default,
    Inheritable,
    Layered,
    ProfileOnly,
    Unconverted,
    VcoModel,
    kind_default,
)
from pyvelo.core.fields import FixedList, KeyField
from pyvelo.core.layers import L

from ..common import SegmentMetadata
from .common import CosMappingInputType
from .rule import QosRule

# CoS Mapping Models (Profile only)


class CosMappingValue(VcoModel):
    value: int
    ratelimit: bool = False


class CosMappingType(VcoModel):
    high: CosMappingValue
    normal: CosMappingValue
    low: CosMappingValue


class CosMapping(VcoModel):
    transactional: CosMappingType = Field(
        default_factory=lambda: CosMappingType(
            high=CosMappingValue(value=20),
            normal=CosMappingValue(value=7),
            low=CosMappingValue(value=1),
        )
    )
    realtime: CosMappingType = Field(
        default_factory=lambda: CosMappingType(
            high=CosMappingValue(value=35),
            normal=CosMappingValue(value=15),
            low=CosMappingValue(value=1),
        )
    )
    bulk: CosMappingType = Field(
        default_factory=lambda: CosMappingType(
            high=CosMappingValue(value=15),
            normal=CosMappingValue(value=5),
            low=CosMappingValue(value=1),
        )
    )
    ls_input_type: CosMappingInputType = "weight"
    override: bool = False


class WebProxy(VcoModel):
    providers: list[Any] = Field(default_factory=list)


class QosSegment(Layered[L]):
    """A segment of the QoS module.

    ``rules`` are the edge's own, with the profile's after them in the effective
    view. ``defaults`` belong to the profile layer. ``cos_mapping`` is local when
    the edge stores it with ``override`` true; the composer takes a stored object
    whatever its flag. ``web_proxy`` is not converted yet.
    """

    segment: SegmentMetadata
    rules: Annotated[
        Additive[list[QosRule]], KeyField("name", alt="rule_logical_id")
    ] = kind_default(factory=list)
    defaults: Annotated[
        ProfileOnly[list[QosRule] | MISSING],
        KeyField("name", alt="rule_logical_id"),
        FixedList(editable=("action",)),
    ] = kind_default(MISSING)
    cos_mapping: Annotated[
        Inheritable[CosMapping],
        ByFlag("override", presence_wins=True),
        Default(factory=CosMapping),
    ] = Field(default=...)
    web_proxy: Unconverted[WebProxy | MISSING] = kind_default(MISSING)
