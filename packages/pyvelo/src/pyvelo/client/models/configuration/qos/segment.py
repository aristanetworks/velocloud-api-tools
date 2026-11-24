from typing import Annotated, Any

from pydantic import MISSING, Field

from pyvelo.core import EdgeOnly, VcoModel
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


class WebProxy(VcoModel):
    providers: list[Any] = Field(default_factory=list)


class QosSegment(VcoModel):
    segment: SegmentMetadata
    rules: Annotated[list[QosRule], KeyField("name")] = Field(default_factory=list)

    # Present on both profile QOS and the edge overlay insert (empty list on
    # overlay). Not ProfileOnly: the UI sends defaults=[] when creating the
    # edge module.
    defaults: Annotated[list[QosRule] | MISSING, KeyField("name")] = MISSING
    cos_mapping: CosMapping | MISSING = MISSING

    # Edge overlay insert: profile catalog lives here, not in ``rules``.
    inherited_rules: Annotated[list[QosRule] | MISSING, EdgeOnly, KeyField("name")] = (
        MISSING
    )
    inherited_defaults: Annotated[
        list[QosRule] | MISSING, EdgeOnly, KeyField("name")
    ] = MISSING
    # Sibling of cosMapping in the UI insert payload. Not OverrideFlag: adding
    # a business-policy rule must not flip this to True.
    cos_mapping_override: Annotated[bool | MISSING, EdgeOnly] = MISSING

    web_proxy: WebProxy | MISSING = MISSING
