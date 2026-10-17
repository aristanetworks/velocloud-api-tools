from typing import Annotated, Any

from pydantic import Field
from pydantic_core import MISSING

from pyvelo.core import ProfileOnly, VcoModel
from pyvelo.core.fields import KeyField, OverrideFlag

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


# Web Proxy Models


class WebProxy(VcoModel):
    providers: list[Any] = Field(default_factory=list)


# Complete Segment Model


class QosSegment(VcoModel):
    segment: SegmentMetadata
    rules: Annotated[list[QosRule], KeyField("name")] = Field(default_factory=list)

    defaults: Annotated[list[QosRule] | MISSING, ProfileOnly, KeyField("name")] = (
        MISSING
    )
    cos_mapping: Annotated[CosMapping | MISSING, ProfileOnly] = MISSING

    name: str | None = None
    web_proxy: WebProxy = Field(default_factory=WebProxy)
