from typing import Annotated

from pydantic import Field

from pyvelo.core import Alias, VcoModel
from pyvelo.core.fields import KeyField

from ..common import SegmentMetadata
from .rules import FirewallOutboundRule


class FirewallSegment(VcoModel):
    segment: SegmentMetadata
    firewall_logging_enabled: Annotated[
        bool | None, Alias("firewall_logging_enabled")
    ] = None
    outbound: Annotated[
        list[FirewallOutboundRule], KeyField("name", alt="rule_logical_id")
    ] = Field(default_factory=list)
