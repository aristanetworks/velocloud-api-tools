from typing import Annotated, Any

from pydantic import MISSING, Field, PrivateAttr

from pyvelo.core import Alias, Layered, Unconverted, kind_default
from pyvelo.core.fields import DefaultingScalar, KeyField
from pyvelo.core.layers import L

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


class FirewallData(Layered[L]):
    # Every direct field is structural or Unconverted: inheritance is declared
    # field by field as each is converted.
    firewall_enabled: Annotated[Unconverted[bool], Alias("firewall_enabled")] = (
        kind_default(True)
    )
    inbound: Annotated[
        Unconverted[list[FirewallInboundRule]],
        KeyField("name", alt="rule_logical_id"),
    ] = kind_default(factory=list)
    inbound_v6: Annotated[
        Unconverted[list[FirewallInboundRuleV6]],
        Alias("inboundV6"),
        KeyField("name", alt="rule_logical_id"),
    ] = kind_default(factory=list)
    segments: Annotated[
        list[FirewallSegment[L]], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)
    syslog_forwarding: Annotated[
        Unconverted[Annotated[bool, DefaultingScalar(bool, default=False)]],
        Alias("syslog_forwarding"),
    ] = kind_default(False)

    firewall_logging_enabled: Annotated[
        Unconverted[Annotated[bool, DefaultingScalar(bool, default=False)]],
        Alias("firewall_logging_enabled"),
    ] = kind_default(False)
    atp_enabled: Annotated[
        Unconverted[Annotated[bool, DefaultingScalar(bool, default=False)]],
        Alias("atp_enabled"),
    ] = kind_default(False)
    stateful_firewall_enabled: Annotated[
        Unconverted[Annotated[bool, DefaultingScalar(bool, default=True)]],
        Alias("stateful_firewall_enabled"),
    ] = kind_default(True)

    security_features: Unconverted[SecurityFeatureSettings] = kind_default(
        factory=SecurityFeatureSettings
    )

    services: Unconverted[FirewallServices] = kind_default(factory=FirewallServices)

    network_protection_settings: Unconverted[NetworkProtectionSettings | MISSING] = (
        kind_default(factory=lambda: MISSING)
    )
    network_protection_settings_v6: Unconverted[NetworkProtectionSettings | MISSING] = (
        kind_default(factory=lambda: MISSING)
    )

    stateful_firewall_settings: Unconverted[StatefulFirewallSettings | MISSING] = (
        kind_default(factory=lambda: MISSING)
    )
    stateful_firewall_settings_v6: Unconverted[StatefulFirewallSettings | MISSING] = (
        kind_default(factory=lambda: MISSING)
    )

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the segments of the profile module this layer inherits from.

        *profile_data* is that module's raw ``data``. It is what :meth:`segment`
        copies a missing segment's descriptor from.
        """
        self._profile_segments = copy_profile_segments(profile_data)

    def segment(self, name: str) -> FirewallSegment[L]:
        """The segment called *name*; a layer that lacks it gets an empty entry.

        The new entry has the profile segment's descriptor and inherits all of
        its content. Raises ``UnboundError`` if the data is not bound to a
        profile (see :meth:`bind_profile`) and ``ValueError`` if the profile has
        no such segment. Use :meth:`get_segment` to look up without creating.
        """
        # FirewallSegment[layer], where the layer is this data's own.
        segment_class: Any = FirewallSegment
        segment_type: type[FirewallSegment[L]] = segment_class[self.layer()]
        return get_or_create_segment(
            self.segments,
            name,
            self._profile_segments,
            "firewall",
            segment_type.model_validate,
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
    ) -> FirewallSegment[L]:
        return find_segment(self.segments, name=name, segment_id=segment_id)
