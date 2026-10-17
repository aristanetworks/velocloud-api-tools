from typing import Annotated

from pydantic import Field

from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField, OverrideFlag

from ..common import SegmentMetadata
from .rules import FirewallOutboundRule


class FirewallSegment(VcoModel):
    segment: SegmentMetadata
    outbound_override: Annotated[bool, OverrideFlag] = False
    firewall_logging_enabled: bool | None = None
    outbound: Annotated[list[FirewallOutboundRule], KeyField("name")] = Field(
        default_factory=list
    )
