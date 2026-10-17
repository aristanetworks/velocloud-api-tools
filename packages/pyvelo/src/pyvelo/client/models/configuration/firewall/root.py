from typing import Annotated

from pydantic import Field
from pydantic_core import MISSING

from pyvelo.core import Alias, ContextDefaults, VcoModel
from pyvelo.core.fields import DefaultingScalar, KeyField

from ..common import _find_segment
from .rules import FirewallInboundRule, FirewallInboundRuleV6
from .security_features import NetworkProtectionSettings, SecurityFeatureSettings
from .segment import FirewallSegment
from .services import FirewallServices
from .stateful import StatefulFirewallSettings


class FirewallData(VcoModel):
    # Common fields (Edge & Profile)
    firewall_enabled: Annotated[bool, Alias("firewall_enabled")] = True
    inbound: Annotated[
        list[FirewallInboundRule],
        KeyField("name"),
        ContextDefaults(profile=lambda: list),
    ] = Field(default_factory=list)
    inbound_v6: Annotated[
        list[FirewallInboundRuleV6],
        Alias("inboundV6"),
        KeyField("name"),
        ContextDefaults(profile=lambda: list),
    ] = Field(default_factory=list)
    segments: Annotated[list[FirewallSegment], KeyField("segment.name")] = Field(
        default_factory=list
    )
    syslog_forwarding: Annotated[
        bool, Alias("syslog_forwarding"), DefaultingScalar(bool, default=False)
    ] = False

    firewall_logging_enabled: Annotated[bool, DefaultingScalar(bool, default=False)] = (
        False
    )
    atp_enabled: Annotated[
        bool, Alias("atp_enabled"), DefaultingScalar(bool, default=False)
    ] = False
    stateful_firewall_enabled: Annotated[
        bool, Alias("stateful_firewall_enabled"), DefaultingScalar(bool, default=True)
    ] = True

    security_features: SecurityFeatureSettings = Field(
        default_factory=SecurityFeatureSettings
    )

    services: FirewallServices = Field(default_factory=FirewallServices)

    network_protection_settings: NetworkProtectionSettings | MISSING = MISSING
    network_protection_settings_v6: NetworkProtectionSettings | MISSING = MISSING

    stateful_firewall_settings: StatefulFirewallSettings | MISSING = MISSING
    stateful_firewall_settings_v6: StatefulFirewallSettings | MISSING = MISSING

    def get_segment(
        self, *, name: str | None = None, segment_id: int | None = None
    ) -> FirewallSegment:
        """"""
        return _find_segment(self.segments, name=name, segment_id=segment_id)
