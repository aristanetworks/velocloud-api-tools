from enum import IntEnum


class TrafficType(IntEnum):
    """QoE traffic classification type."""

    VOICE = 0
    VIDEO = 1
    TRANSACTIONAL = 2

    @property
    def label(self) -> str:
        return self.name.lower()


class QualityState(IntEnum):
    """QoE quality state for a link or traffic type."""

    OFFLINE = 0
    UNKNOWN = 1
    RED = 2
    YELLOW = 3
    GREEN = 4

    @property
    def label(self) -> str:
        return self.name


class LinkQualityMetric(IntEnum):
    """Per-sample metric index in link quality events."""

    LATENCY_MS_TX = 0
    LATENCY_MS_RX = 1
    LOSS_PCT_RX = 2
    LOSS_PCT_TX = 3
    JITTER_MS_TX = 4
    JITTER_MS_RX = 5

    @property
    def label(self) -> str:
        return self.name.lower()


class EnhancementAction(IntEnum):
    """SD-WAN enhancement action applied to a traffic flow."""

    NONE = 0
    AVOID = 1
    JITTER_BUFFER = 2
    ERROR_CORRECTION = 3
    FORWARD_ERROR_CORRECTION = 4

    @property
    def label(self) -> str:
        _labels = {4: "fec"}
        return _labels.get(self.value, self.name.lower())
