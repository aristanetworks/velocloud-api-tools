"""The QoE latency thresholds of the voice, video and transactional traffic types."""

from typing import Annotated

from pydantic import MISSING, Field

from pyvelo.core import Alias, ConfigModel


class QoeThreshold(ConfigModel):
    yellow: int
    red: int


class QoeLatencyThreshold(ConfigModel):
    latency: QoeThreshold


class QoeSettings(ConfigModel):
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
