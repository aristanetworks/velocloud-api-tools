from typing import Annotated, Any

from pydantic import Field, PrivateAttr

from pyvelo.core import ByPresence, Default, Inheritable, Layered, VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.layers import L, Profile

from pyvelo.client.models.configuration.common import (
    bind_profile_layers,
    find_segment,
    get_or_create_segment,
    profile_segment_id,
    profile_segment_name,
)
from pyvelo.client.models.configuration.profile_segments import template_default
from .common import ServiceRateLimitInputType
from .qoe import QoeSettings
from .segment import QosSegment


class ServiceRateLimit(VcoModel):
    enabled: bool = False
    input_type: ServiceRateLimitInputType
    value: int = 1


class QosClientConnector(VcoModel):
    # Keeping as flexible dict for now - add specific fields as needed
    data: dict[str, Any] = Field(default_factory=dict)


class QosData(Layered[L]):
    """The QoS module's data.

    The three module-level values are ``Inheritable`` and stored by presence:
    the edge may store its own, and a layer that stores none displays the
    value the packaged profile template holds (``None`` for
    ``qos_client_connector``).
    """

    segments: Annotated[
        list[QosSegment[L]], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)
    service_rate_limit: Annotated[
        Inheritable[ServiceRateLimit],
        ByPresence(),
        Default(factory=template_default(ServiceRateLimit, "QOS", "serviceRateLimit")),
    ] = Field(default=...)
    qoe: Annotated[
        Inheritable[QoeSettings],
        ByPresence(),
        Default(factory=template_default(QoeSettings, "QOS", "qoe")),
    ] = Field(default=...)
    qos_client_connector: Annotated[
        Inheritable[QosClientConnector | None],
        ByPresence(),
        Default(None),
    ] = Field(default=...)

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the profile module this layer inherits from.

        *profile_data* is that module's raw ``data`` (anything else, as for an
        edge whose profile has no QoS module, is a profile with no segments).
        Its segments are what :meth:`segment` copies a missing segment's
        descriptor from. On the edge layer the data, and the segments in it,
        are also bound to the profile layer read from it (a ``QosData[Profile]``),
        which is where ``localize`` copies from: see
        ``pyvelo.core.owned.bind_layers``. A segment :meth:`segment` adds
        afterwards is bound as well. Raises ``ValidationError`` for a profile
        module that does not parse.
        """
        self._profile_segments = bind_profile_layers(
            self, profile_data, QosData[Profile]
        )

    def segment(self, name: str) -> QosSegment[L]:
        """The segment called *name*; a layer that lacks it gets an empty entry.

        The new entry has the profile segment's descriptor and inherits all of
        its content. Raises ``UnboundError`` if the data is not bound to a
        profile (see :meth:`bind_profile`) and ``ValueError`` if the profile has
        no such segment. Use :meth:`get_segment` to look up without creating.
        """
        # QosSegment[layer], where the layer is this data's own.
        segment_class: Any = QosSegment
        segment_type: type[QosSegment[L]] = segment_class[self.layer()]
        return get_or_create_segment(
            self.segments,
            name,
            self._profile_segments,
            "QOS",
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
    ) -> QosSegment[L]:
        return find_segment(self.segments, name=name, segment_id=segment_id)
