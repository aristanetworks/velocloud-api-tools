from typing import Annotated

from pyvelo.core import Additive, Alias, Layered, Unconverted, kind_default
from pyvelo.core.fields import KeyField
from pyvelo.core.layers import L

from ..common import SegmentMetadata
from .rules import FirewallOutboundRule


class FirewallSegment(Layered[L]):
    segment: SegmentMetadata
    firewall_logging_enabled: Annotated[
        Unconverted[bool | None], Alias("firewall_logging_enabled")
    ] = kind_default(None)
    outbound: Annotated[
        Additive[list[FirewallOutboundRule]],
        KeyField("name", alt="rule_logical_id"),
    ] = kind_default(factory=list)
