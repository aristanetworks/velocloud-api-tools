"""Shared testing helpers for pyvelo-intent and downstream packages."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import override

from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.enterprise.network_segment import NetworkSegmentData
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResult
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    GetConfiguration,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    UpdateConfigurationModule,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.client.schemas.enterprise import (
    GetEnterpriseEdges,
    GetEnterpriseEdgesItem,
    GetEnterpriseNetworkSegments,
    NetworkSegment,
)
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.client.vco_client import VcoClient


def make_firewall_segment(
    name: str = "Global Segment",
    segment_id: int = 0,
    rules: list[FirewallOutboundRule] | None = None,
) -> FirewallSegment:
    return FirewallSegment(
        segment=SegmentMetadata(
            name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
        ),
        outbound=rules or [],
    )


def make_firewall_module(
    mod_id: int = 1,
    config_id: int = 100,
    segments: list[FirewallSegment] | None = None,
) -> FirewallConfigurationModule:
    return FirewallConfigurationModule(
        name="firewall",
        data=FirewallData(segments=segments or []),
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        id_=mod_id,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=config_id,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


class MockVcoClient(VcoClient):
    """Mock VCO client that captures all API calls."""

    def __init__(
        self,
        stack: GetEdgeConfigurationStackResult | None = None,
        edges: Sequence[GetEnterpriseEdgesItem] | None = None,
    ) -> None:
        super().__init__("vco.example.invalid", "test-token")
        self.stack: GetEdgeConfigurationStackResult | None = stack
        self._edges: Sequence[GetEnterpriseEdgesItem] = (
            edges if edges is not None else []
        )
        self.calls: list[tuple[str, object]] = []
        self.update_requests: list[UpdateConfigurationModule] = []

    @override
    async def get_edge_configuration_stack(
        self, request: GetEdgeConfigurationStack
    ) -> GetEdgeConfigurationStackResult:
        self.calls.append(("get_edge_configuration_stack", request))
        if self.stack is None:
            raise RuntimeError("No configuration stack was provided to the mock client")
        return self.stack

    @override
    async def get_configuration(self, request: GetConfiguration) -> ConfigurationBase:
        self.calls.append(("get_configuration", request))
        if self.stack is None:
            raise RuntimeError("No configuration stack was provided to the mock client")
        return self.stack[1]

    @override
    async def get_enterprise_edges(
        self, request: GetEnterpriseEdges
    ) -> Sequence[GetEnterpriseEdgesItem]:
        self.calls.append(("get_enterprise_edges", request))
        return self._edges

    @override
    async def get_enterprise_configurations(
        self, request: GetEnterpriseConfigurations
    ) -> Sequence[EnterpriseConfigurationProfile]:
        self.calls.append(("get_enterprise_configurations", request))
        return []

    @override
    async def get_enterprise_network_segments(
        self, request: GetEnterpriseNetworkSegments
    ) -> Sequence[NetworkSegment]:
        self.calls.append(("get_enterprise_network_segments", request))
        return [
            NetworkSegment.model_construct(
                id_=1,
                name="Global Segment",
                logical_id=uuid.UUID(int=0),
                type_=NetworkSegmentType.REGULAR,
                data=NetworkSegmentData.model_construct(segment_id=0),
            )
        ]

    @override
    async def update_configuration_module(
        self, request: UpdateConfigurationModule
    ) -> None:
        self.calls.append(("update_configuration_module", request))
        self.update_requests.append(request)

    @override
    async def insert_configuration_module(
        self, request: InsertConfigurationModule
    ) -> InsertConfigurationModuleResult:
        self.calls.append(("insert_configuration_module", request))
        return InsertConfigurationModuleResult(id_=1)
