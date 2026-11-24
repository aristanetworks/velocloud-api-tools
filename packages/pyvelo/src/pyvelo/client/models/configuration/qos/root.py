from typing import Annotated, Any

from pydantic import MISSING, Field, PrivateAttr

from pyvelo.core import Layered, Unconverted, VcoModel, kind_default
from pyvelo.core.fields import KeyField
from pyvelo.core.layers import L

from ..common import (
    copy_profile_segments,
    find_segment,
    get_or_create_segment,
    profile_segment_id,
    profile_segment_name,
)
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
    segments: Annotated[
        list[QosSegment[L]], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)
    service_rate_limit: Unconverted[ServiceRateLimit | MISSING] = kind_default(MISSING)
    qoe: Unconverted[QoeSettings | MISSING] = kind_default(MISSING)
    qos_client_connector: Unconverted[QosClientConnector | MISSING] = kind_default(
        MISSING
    )

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the segments of the profile module this layer inherits from.

        *profile_data* is that module's raw ``data``. It is what :meth:`segment`
        copies a missing segment's descriptor from.
        """
        self._profile_segments = copy_profile_segments(profile_data)

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
