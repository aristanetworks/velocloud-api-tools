from typing import Annotated

from pydantic import MISSING, Field

from pyvelo.core import Alias, VcoModel


class QoeThreshold(VcoModel):
    yellow: int
    red: int


class QoeLatencyThreshold(VcoModel):
    latency: QoeThreshold


class QoeSettings(VcoModel):
    voice: QoeLatencyThreshold = Field(
        default_factory=lambda: QoeLatencyThreshold(
            latency=QoeThreshold(yellow=25, red=65)
        )
    )
    video: QoeLatencyThreshold = Field(
        default_factory=lambda: QoeLatencyThreshold(
            latency=QoeThreshold(yellow=25, red=65)
        )
    )
    transactional: Annotated[QoeLatencyThreshold, Alias("trans")] = Field(
        default_factory=lambda: QoeLatencyThreshold(
            latency=QoeThreshold(yellow=50, red=80)
        )
    )
    event_time: str | None | MISSING = MISSING
