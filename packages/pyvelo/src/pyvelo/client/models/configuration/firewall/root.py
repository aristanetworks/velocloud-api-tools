from typing import Annotated, Any

from pydantic import MISSING, Field, PrivateAttr

from pyvelo.core import Alias, ContextDefaults, VcoModel
from pyvelo.core.fields import DefaultingScalar, KeyField

from ..common import (
    copy_profile_segments,
    find_segment,
    get_or_create_segment,
    profile_segment_id,
    profile_segment_name,
)
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
        KeyField("name", alt="rule_logical_id"),
        ContextDefaults(profile=lambda: list),
    ] = Field(default_factory=list)
    inbound_v6: Annotated[
        list[FirewallInboundRuleV6],
        Alias("inboundV6"),
        KeyField("name", alt="rule_logical_id"),
        ContextDefaults(profile=lambda: list),
    ] = Field(default_factory=list)
    segments: Annotated[
        list[FirewallSegment], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)
    syslog_forwarding: Annotated[
        bool, Alias("syslog_forwarding"), DefaultingScalar(bool, default=False)
    ] = False

    firewall_logging_enabled: Annotated[
        bool, Alias("firewall_logging_enabled"), DefaultingScalar(bool, default=False)
    ] = False
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

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the segments of the profile module this layer inherits from.

        *profile_data* is that module's raw ``data``. It is what :meth:`segment`
        copies a missing segment's descriptor from.
        """
        self._profile_segments = copy_profile_segments(profile_data)

    def segment(self, name: str) -> FirewallSegment:
        """The segment called *name*; a layer that lacks it gets an empty entry.

        The new entry has the profile segment's descriptor and inherits all of
        its content. Raises ``UnboundError`` if the data is not bound to a
        profile (see :meth:`bind_profile`) and ``ValueError`` if the profile has
        no such segment. Use :meth:`get_segment` to look up without creating.
        """
        return get_or_create_segment(
            self.segments,
            name,
            self._profile_segments,
            "firewall",
            FirewallSegment.model_validate,
            self.config_context,
        )

    def profile_segment_name(self, selector: object) -> str | None:
        """The bound profile's name for the segment *selector* names, or None.

        See :func:`~pyvelo.client.models.configuration.common.profile_segment_name`.
        Raises ``UnboundError`` if the data is not bound to a profile.
        """
        return profile_segment_name(self._profile_segments, selector)

    def profile_segment_id(self, selector: object) -> object | None:
        """The ``segmentId`` of the bound profile's segment *selector* names, or None.

        See :func:`~pyvelo.client.models.configuration.common.profile_segment_id`.
        Raises ``UnboundError`` if the data is not bound to a profile.
        """
        return profile_segment_id(self._profile_segments, selector)

    def get_segment(
        self, *, name: str | None = None, segment_id: int | None = None
    ) -> FirewallSegment:
        return find_segment(self.segments, name=name, segment_id=segment_id)
