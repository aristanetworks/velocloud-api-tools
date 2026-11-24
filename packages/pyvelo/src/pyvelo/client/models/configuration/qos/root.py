from typing import Annotated, Any

from pydantic import MISSING, Field, PrivateAttr

from pyvelo.core import Alias, EdgeOnly, VcoModel
from pyvelo.core.fields import KeyField

from ..common import (
    copy_profile_segments,
    find_segment,
    get_or_create_segment,
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


class QosData(VcoModel):
    segments: Annotated[list[QosSegment], KeyField("segment.name")] = Field(
        default_factory=list
    )
    service_rate_limit: ServiceRateLimit | MISSING = MISSING
    qoe: QoeSettings | MISSING = MISSING
    qos_client_connector: QosClientConnector | MISSING = MISSING
    # Edge overlay flags. Not OverrideFlag: adding a rule must not flip them.
    service_rate_limit_override: Annotated[bool | MISSING, EdgeOnly] = MISSING
    qos_client_connector_override: Annotated[bool | MISSING, EdgeOnly] = MISSING
    customizable_qoe_override: Annotated[
        bool | MISSING, EdgeOnly, Alias("customizableQOEOverride")
    ] = MISSING

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the segments of the profile module this layer inherits from.

        *profile_data* is that module's raw ``data``. It is what :meth:`segment`
        copies a missing segment's descriptor from.
        """
        self._profile_segments = copy_profile_segments(profile_data)

    def segment(self, name: str) -> QosSegment:
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
            "QOS",
            QosSegment.model_validate,
            self.config_context,
        )

    def get_segment(
        self, *, name: str | None = None, segment_id: int | None = None
    ) -> QosSegment:
        return find_segment(self.segments, name=name, segment_id=segment_id)
