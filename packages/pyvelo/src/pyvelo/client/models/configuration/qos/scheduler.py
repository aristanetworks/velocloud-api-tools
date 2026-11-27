"""The receive and transmit schedulers of a QoS rule's service class."""

from typing import Annotated

from pydantic import Field

from pyvelo.core import Alias, ConfigModel

from .common import FlowPriority, ServiceClass


class QosScheduler(ConfigModel):
    bandwidth: int = -1
    bandwidth_cap_percent: Annotated[int, Alias("bandwidthCapPct")] = -1
    queue_length: Annotated[int, Alias("queueLen")] = -1
    burst: int = -1
    latency: int = -1
    priority: FlowPriority = "normal"


class QosSettings(ConfigModel):
    type_: Annotated[ServiceClass, Alias("type")] = "transactional"
    rx_scheduler: QosScheduler = Field(default_factory=QosScheduler)
    tx_scheduler: QosScheduler = Field(default_factory=QosScheduler)
