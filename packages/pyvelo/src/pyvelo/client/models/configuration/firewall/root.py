"""The ``firewall`` module's data, and the local form of its inheritable groups.

``profile_false_wins`` is how the VCO combines ``firewall_enabled`` across layers.
"""

from typing import Annotated, Any, ClassVar

from pydantic import Field, PrivateAttr

from pyvelo.client.models.configuration.common import (
    bind_profile_layers,
    find_segment,
    get_or_create_segment,
    profile_segment_id,
    profile_segment_name,
)
from pyvelo.client.models.configuration.profile_segments import template_default
from pyvelo.core import (
    Alias,
    Authority,
    ByPresence,
    Default,
    Inheritable,
    Layered,
    MemberOf,
    Unconverted,
    VcoModel,
    kind_default,
)
from pyvelo.core.fields import DefaultingScalar, KeyField
from pyvelo.core.layers import L, Profile
from pyvelo.core.owned import OwnedGroup

from .rules import FirewallInboundRule, FirewallInboundRuleV6
from .security_features import NetworkProtectionSettings, SecurityFeatureSettings
from .segment import FirewallSegment
from .services import FirewallServices
from .stateful import StatefulFirewallSettings


def profile_false_wins(profile_value: object) -> bool:
    """The VCO's rule for ``firewall_enabled``: a profile that has it off turns it off for every edge."""
    return profile_value is False


class StatefulLocal(VcoModel):
    """What the group ``stateful`` stores when it is local: every member, all required."""

    stateful_firewall_settings: StatefulFirewallSettings
    stateful_firewall_settings_v6: StatefulFirewallSettings
    stateful_firewall_enabled: bool


class NetworkProtectionLocal(VcoModel):
    """What the group ``network_protection`` stores when it is local: every member, all required."""

    network_protection_settings: NetworkProtectionSettings
    network_protection_settings_v6: NetworkProtectionSettings


class AtpLocal(VcoModel):
    """What the group ``atp`` stores when it is local: every member, all required."""

    atp_enabled: bool
    security_features: SecurityFeatureSettings


class FirewallData(Layered[L]):
    """The firewall module's data.

    The module-level values are ``Inheritable``: the edge may store its own, in
    three groups whose members change ownership together (``stateful``,
    ``network_protection`` and ``atp``). Each has a display default, read from
    the packaged profile template, for a layer that stores nothing. ``inbound``
    and ``inbound_v6`` are not declared yet.
    """

    firewall_enabled: Annotated[
        Inheritable[bool],
        Alias("firewall_enabled"),
        ByPresence(),
        Default(True),
        Authority(profile_false_wins),
    ] = Field(default=...)
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
        Inheritable[Annotated[bool, DefaultingScalar(bool, default=False)]],
        Alias("syslog_forwarding"),
        ByPresence(),
        Default(False),
    ] = Field(default=...)

    firewall_logging_enabled: Annotated[
        Inheritable[
            Annotated[bool | None, DefaultingScalar(bool | None, default=None)]
        ],
        Alias("firewall_logging_enabled"),
        ByPresence(),
        Default(False),
    ] = Field(default=...)
    atp_enabled: Annotated[
        Inheritable[Annotated[bool, DefaultingScalar(bool, default=False)]],
        Alias("atp_enabled"),
        ByPresence(),
        MemberOf("atp"),
        Default(False),
    ] = Field(default=...)
    stateful_firewall_enabled: Annotated[
        Inheritable[Annotated[bool, DefaultingScalar(bool, default=True)]],
        Alias("stateful_firewall_enabled"),
        ByPresence(),
        MemberOf("stateful"),
        Default(False),
    ] = Field(default=...)

    security_features: Annotated[
        Inheritable[SecurityFeatureSettings],
        ByPresence(),
        MemberOf("atp"),
        Default(
            factory=template_default(
                SecurityFeatureSettings, "firewall", "securityFeatures"
            )
        ),
    ] = Field(default=...)

    services: Annotated[
        Inheritable[FirewallServices],
        ByPresence(),
        Default(factory=template_default(FirewallServices, "firewall", "services")),
    ] = Field(default=...)

    network_protection_settings: Annotated[
        Inheritable[NetworkProtectionSettings],
        ByPresence(),
        MemberOf("network_protection"),
        Default(
            factory=template_default(
                NetworkProtectionSettings, "firewall", "networkProtectionSettings"
            )
        ),
    ] = Field(default=...)
    network_protection_settings_v6: Annotated[
        Inheritable[NetworkProtectionSettings],
        ByPresence(),
        MemberOf("network_protection"),
        Default(
            factory=template_default(
                NetworkProtectionSettings, "firewall", "networkProtectionSettingsV6"
            )
        ),
    ] = Field(default=...)

    stateful_firewall_settings: Annotated[
        Inheritable[StatefulFirewallSettings],
        ByPresence(),
        MemberOf("stateful"),
        Default(
            factory=template_default(
                StatefulFirewallSettings, "firewall", "statefulFirewallSettings"
            )
        ),
    ] = Field(default=...)
    stateful_firewall_settings_v6: Annotated[
        Inheritable[StatefulFirewallSettings],
        ByPresence(),
        MemberOf("stateful"),
        Default(
            factory=template_default(
                StatefulFirewallSettings, "firewall", "statefulFirewallSettingsV6"
            )
        ),
    ] = Field(default=...)

    stateful: ClassVar[OwnedGroup] = OwnedGroup(
        "stateful", anchor="stateful_firewall_settings", bundle=StatefulLocal
    )
    network_protection: ClassVar[OwnedGroup] = OwnedGroup(
        "network_protection",
        anchor="network_protection_settings",
        bundle=NetworkProtectionLocal,
    )
    atp: ClassVar[OwnedGroup] = OwnedGroup("atp", anchor="atp_enabled", bundle=AtpLocal)

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the profile module this layer inherits from.

        *profile_data* is that module's raw ``data`` (anything else, as for an
        edge whose profile has no firewall module, is a profile with no
        segments). Its segments are what :meth:`segment` copies a missing
        segment's descriptor from. On the edge layer the data, and the segments
        in it, are also bound to the profile layer read from it (a
        ``FirewallData[Profile]``), which is where ``localize`` copies from: see
        ``pyvelo.core.owned.bind_layers``. A segment :meth:`segment` adds
        afterwards is bound as well. Raises ``ValidationError`` for a profile
        module that does not parse.
        """
        self._profile_segments = bind_profile_layers(
            self, profile_data, FirewallData[Profile]
        )

    def segment(self, name: str) -> FirewallSegment[L]:
        """The segment called *name*; a layer that lacks it gets an empty entry.

        The new entry has the profile segment's descriptor and inherits all of
        its content, and on the edge layer it is bound to the profile's segment,
        so its keys can be localized. Raises ``UnboundError`` if the data is not
        bound to a profile (see :meth:`bind_profile`) and ``ValueError`` if the
        profile has no such segment. Use :meth:`get_segment` to look up without creating.
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
            owner=self,
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
