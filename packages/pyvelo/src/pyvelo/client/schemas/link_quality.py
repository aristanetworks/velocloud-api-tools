from typing import Annotated

from pydantic import ConfigDict
from pydantic_core import MISSING

from pyvelo.client.enums.link_quality import (
    EnhancementAction,
    LinkQualityMetric,
    QualityState,
    TrafficType,
)
from pyvelo.core import ApiInterval, VcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity

__all__ = [
    "GetLinkQualityEvents",
    "GetLinkQualityEventsResult",
    "GetLinkQualityEventsResultItem",
    "LinkQualityData",
    "LinkQualityMetricEntry",
    "LinkQualitySample",
    "LinkQualitySampleDetail",
    "LinkQualitySampleMetadata",
]


class GetLinkQualityEvents(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")]
    interval: ApiInterval
    max_samples: int | MISSING = MISSING
    minutes_per_sample: int | MISSING = MISSING
    individual_scores: bool | MISSING = MISSING
    links: list[int] | MISSING = MISSING


# --- Result models ---


class LinkQualitySampleDetail(VcoModel):
    """Raw link metrics for a single timeseries sample."""

    latency_ms_tx: float | MISSING = MISSING
    latency_ms_rx: float | MISSING = MISSING
    loss_pct_rx: float | MISSING = MISSING
    loss_pct_tx: float | MISSING = MISSING
    jitter_ms_tx: float | MISSING = MISSING
    jitter_ms_rx: float | MISSING = MISSING


class LinkQualityMetricEntry(VcoModel):
    """Per-metric pre/post enhancement state."""

    traffic_type: TrafficType
    metric: LinkQualityMetric
    action: EnhancementAction
    before_state: QualityState
    after_state: QualityState


class MetricStateEntry(VcoModel):
    """Before/after quality state and enhancement action for a single metric."""

    before: QualityState | MISSING = MISSING
    after: QualityState
    action: EnhancementAction | MISSING = MISSING


class LinkQualitySampleMetadata(VcoModel):
    """Metadata for a timeseries sample."""

    detail: LinkQualitySampleDetail | MISSING = MISSING
    metrics: list[LinkQualityMetricEntry] | MISSING = MISSING
    state_map: (
        dict[TrafficType, dict[LinkQualityMetric, MetricStateEntry]] | MISSING
    ) = MISSING


class LinkQualitySample(VcoModel):
    """A single timeseries point.

    Overall samples omit ``before`` and ``metadata.detail``/``metadata.metrics``.
    """

    timestamp: int
    before: dict[TrafficType, QualityState] | MISSING = MISSING
    after: dict[TrafficType, QualityState]
    metadata: LinkQualitySampleMetadata | MISSING = MISSING


class LinkQualityData(VcoModel):
    """Quality data for one link or the overall aggregate.

    distribution maps traffic type -> quality state -> percentage (0.0-1.0).
    score maps traffic type -> QoE score (0-10).
    """

    timeseries: list[LinkQualitySample] | MISSING = MISSING
    distribution: dict[TrafficType, dict[QualityState, float]] | MISSING = MISSING
    score: dict[TrafficType, float] | MISSING = MISSING
    total_score: float | MISSING = MISSING
    sample_length: int | MISSING = MISSING
    sample_count: int | MISSING = MISSING


class GetLinkQualityEventsResult(VcoModel):
    """Link quality result with overall aggregate and per-link data.

    Per-link data is stored as extra fields keyed by each link's internalId
    (UUID string). Use ``link_entries`` to access them as typed objects.
    """

    model_config = ConfigDict(extra="allow")
    overall_link_quality: LinkQualityData | MISSING = MISSING

    @property
    def link_entries(self) -> dict[str, LinkQualityData]:
        """Per-link quality data keyed by link internalId."""
        if not self.model_extra:
            return {}
        return {
            k: LinkQualityData.model_validate(v) for k, v in self.model_extra.items()
        }


# Backwards-compatible alias
GetLinkQualityEventsResultItem = LinkQualityData
