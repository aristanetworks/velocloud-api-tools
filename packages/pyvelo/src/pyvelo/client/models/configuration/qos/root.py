from typing import Annotated, Any

from pydantic import MISSING, Field

from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField

from ..common import _find_segment
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

    def get_segment(
        self, *, name: str | None = None, segment_id: int | None = None
    ) -> QosSegment:
        return _find_segment(self.segments, name=name, segment_id=segment_id)
