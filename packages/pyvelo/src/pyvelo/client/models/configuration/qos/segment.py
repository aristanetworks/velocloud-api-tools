from typing import Annotated, Any

from pydantic import MISSING, Field

from pyvelo.core import Layered, Unconverted, VcoModel, kind_default
from pyvelo.core.fields import FixedList, KeyField, OverrideFlag
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
    override: Annotated[bool, OverrideFlag] = False


class WebProxy(VcoModel):
    providers: list[Any] = Field(default_factory=list)


class QosSegment(Layered[L]):
    segment: SegmentMetadata
    rules: Annotated[
        Unconverted[list[QosRule]], KeyField("name", alt="rule_logical_id")
    ] = kind_default(factory=list)
    defaults: Annotated[
        Unconverted[list[QosRule] | MISSING],
        KeyField("name", alt="rule_logical_id"),
        FixedList(editable=("action",)),
    ] = kind_default(MISSING)
    cos_mapping: Unconverted[CosMapping | MISSING] = kind_default(MISSING)
    web_proxy: Unconverted[WebProxy | MISSING] = kind_default(MISSING)
