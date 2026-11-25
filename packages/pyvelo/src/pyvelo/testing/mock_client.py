"""Testing helpers for the configuration pipeline: a client over a ``ModuleStore`` and firewall module builders."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self, cast, overload, override

from pydantic import MISSING

from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.enterprise.network_segment import NetworkSegmentData
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    GetConfiguration,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    UpdateConfigurationModule,
)
from pyvelo.client.schemas.enterprise import (
    GetEnterpriseEdges,
    GetEnterpriseEdgesItem,
    GetEnterpriseNetworkSegments,
    NetworkSegment,
)
from pyvelo.client.vco_client import VcoClient
from pyvelo.core.layers import Edge, Profile
from pyvelo.testing.fixtures import make_transport
from pyvelo.testing.module_store import ModuleStore, configuration_row, module_row

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
    from pyvelo.client.operations.edge import GetEdgeConfigurationStackResult
    from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
    from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
    from pyvelo.config.state import ConfigState
    from pyvelo.core import ConfigContext

_HOST = "vco.example.invalid"
_GLOBAL_SEGMENT_OBJECT_ID = 1
_GLOBAL_SEGMENT_LOGICAL_ID = uuid.UUID(int=0)


@overload
def make_firewall_segment(
    name: str = ...,
    segment_id: int = ...,
    rules: list[FirewallOutboundRule] | None = ...,
    *,
    layer: type[Edge] = ...,
) -> FirewallSegment[Edge]: ...
@overload
def make_firewall_segment(
    name: str = ...,
    segment_id: int = ...,
    rules: list[FirewallOutboundRule] | None = ...,
    *,
    layer: type[Profile],
) -> FirewallSegment[Profile]: ...
def make_firewall_segment(
    name: str = "Global Segment",
    segment_id: int = 0,
    rules: list[FirewallOutboundRule] | None = None,
    *,
    layer: type[Edge | Profile] = Edge,
) -> FirewallSegment[Edge] | FirewallSegment[Profile]:
    """A firewall segment of the layer *layer* (``Edge`` unless said otherwise)."""
    segment_type = FirewallSegment[Edge] if layer is Edge else FirewallSegment[Profile]
    return segment_type(
        segment=SegmentMetadata(
            name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
        ),
        outbound=rules or [],
    )


def _firewall_row(
    mod_id: int, config_id: int, data: FirewallData[Edge] | FirewallData[Profile]
) -> dict[str, object]:
    """The row of a firewall module as read, with *data* as its data."""
    return {
        "name": "firewall",
        "data": data,
        "created": datetime(2024, 1, 1, tzinfo=UTC),
        "modified": datetime(2024, 1, 1, tzinfo=UTC),
        "id": mod_id,
        "type": ConfigurationModuleType.ENTERPRISE,
        "description": None,
        "configurationId": config_id,
        "enterpriseLogicalId": None,
        "schemaVersion": None,
        "version": None,
        "metadata": None,
    }


@overload
def make_firewall_module(
    mod_id: int = ...,
    config_id: int = ...,
    segments: list[FirewallSegment[Edge]] | None = ...,
    *,
    layer: type[Edge] = ...,
) -> FirewallConfigurationModule[Edge]: ...
@overload
def make_firewall_module(
    mod_id: int = ...,
    config_id: int = ...,
    segments: list[FirewallSegment[Profile]] | None = ...,
    *,
    layer: type[Profile],
) -> FirewallConfigurationModule[Profile]: ...
def make_firewall_module(
    mod_id: int = 1,
    config_id: int = 100,
    segments: list[FirewallSegment[Edge]]
    | list[FirewallSegment[Profile]]
    | None = None,
    *,
    layer: type[Edge | Profile] = Edge,
) -> FirewallConfigurationModule[Edge] | FirewallConfigurationModule[Profile]:
    """A firewall module of the layer *layer* (``Edge`` unless said otherwise), with *segments*."""
    if layer is Edge:
        data = FirewallData[Edge](
            segments=cast("list[FirewallSegment[Edge]]", segments or [])
        )
        return FirewallConfigurationModule[Edge].model_validate(
            _firewall_row(mod_id, config_id, data)
        )
    profile_data = FirewallData[Profile](
        segments=cast("list[FirewallSegment[Profile]]", segments or [])
    )
    return FirewallConfigurationModule[Profile].model_validate(
        _firewall_row(mod_id, config_id, profile_data)
    )


def _register_stack(
    store: ModuleStore, edge: EdgeConfiguration, profile: EnterpriseConfigurationProfile
) -> None:
    """Have *store* answer the stack of every edge with the two configurations."""
    match (edge.id_, profile.id_):
        case (int() as edge_id, int() as profile_id):
            store.add_stack(edge_id, profile_id)
        case _:
            raise ValueError(
                "the mock client can serve a stack only of configurations that "
                "have an ID; give the edge and profile configurations an id_"
            )


class MockVcoClient(VcoClient):
    """Mock VCO client that records its API calls and serves configurations from a store.

    An edge's configuration stack, module updates, inserts and the
    configurations the store holds go through the real client code to
    ``store``, a :class:`~pyvelo.testing.ModuleStore`; seed it with
    :meth:`seed`, or build the client with :meth:`for_state`. A ``stack`` seeds
    it with both its configurations and is served for every edge. A load reads
    the stack from the store, as new objects each time, so a second load sees
    what a save stored. The store also holds the one segment
    ``get_enterprise_network_segments`` answers, as an object a ref row can
    point at; :meth:`add_service` adds the services. The other calls are
    answered here.
    """

    store: ModuleStore

    def __init__(
        self,
        stack: GetEdgeConfigurationStackResult | None = None,
        edges: Sequence[GetEnterpriseEdgesItem] | None = None,
        *,
        conditional_check: bool = False,
    ) -> None:
        self.store = ModuleStore(conditional_check=conditional_check)
        super().__init__(_HOST, "test-token", transport=make_transport(self.store))
        self.store.add_object(
            _GLOBAL_SEGMENT_OBJECT_ID,
            logical_id=str(_GLOBAL_SEGMENT_LOGICAL_ID),
            name="Global Segment",
            type_=NetworkSegmentType.REGULAR.value,
            object_="NETWORK_SEGMENT",
            data={"segmentId": 0},
        )
        self.stack: GetEdgeConfigurationStackResult | None = stack
        self._edges: Sequence[GetEnterpriseEdgesItem] = (
            edges if edges is not None else []
        )
        self.calls: list[tuple[str, object]] = []
        self.update_requests: list[UpdateConfigurationModule] = []
        self.insert_requests: list[InsertConfigurationModule] = []
        if stack is not None:
            _ = self.seed(*stack)
            _register_stack(self.store, *stack)

    def seed(
        self, *configs: EdgeConfiguration | EnterpriseConfigurationProfile
    ) -> Self:
        """Put *configs* and their modules in the store, as the VCO holds them.

        A module not inserted yet (``is_new``) is left out.
        """
        for config in configs:
            modules = [] if config.modules is MISSING else config.modules
            self.store.add_configuration(
                {
                    **configuration_row(config),
                    "modules": [module_row(m) for m in modules if not m.is_new],
                }
            )
        return self

    def add_service(
        self, object_id: int, *, logical_id: str, name: str, type_: str
    ) -> None:
        """Have the store hold the network service *object_id*, which a ref row can point at."""
        self.store.add_object(object_id, logical_id=logical_id, name=name, type_=type_)

    @classmethod
    def for_state(cls, state: ConfigState, *, conditional_check: bool = False) -> Self:
        """A client whose store holds the configurations of *state*: its edge one, if any, and its profile one.

        A state with an edge configuration also gets the stack of both, for
        every edge.
        """
        client = cls(conditional_check=conditional_check)
        if state.edge_config is not None:
            _ = client.seed(state.edge_config)
            _register_stack(client.store, state.edge_config, state.profile_config)
        return client.seed(state.profile_config)

    @override
    async def get_edge_configuration_stack(
        self, request: GetEdgeConfigurationStack
    ) -> GetEdgeConfigurationStackResult:
        self.calls.append(("get_edge_configuration_stack", request))
        if not self.store.stacks:
            raise RuntimeError("No configuration stack was provided to the mock client")
        return await super().get_edge_configuration_stack(request)

    @override
    async def get_configuration(
        self, request: GetConfiguration
    ) -> EdgeConfiguration | EnterpriseConfigurationProfile:
        """The configuration from the store when it holds it; else the stack's profile."""
        self.calls.append(("get_configuration", request))
        if request.id_ in self.store.configurations:
            return await super().get_configuration(request)
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
                id_=_GLOBAL_SEGMENT_OBJECT_ID,
                name="Global Segment",
                logical_id=_GLOBAL_SEGMENT_LOGICAL_ID,
                type_=NetworkSegmentType.REGULAR,
                data=NetworkSegmentData.model_construct(segment_id=0),
            )
        ]

    @override
    async def update_configuration_module(
        self,
        request: UpdateConfigurationModule,
        *,
        level: ConfigContext | None = None,
    ) -> AnyStoredModule | None:
        self.calls.append(("update_configuration_module", request))
        self.update_requests.append(request)
        return await super().update_configuration_module(request, level=level)

    @override
    async def insert_configuration_module(
        self,
        request: InsertConfigurationModule,
        *,
        level: ConfigContext | None = None,
    ) -> InsertConfigurationModuleResult | AnyStoredModule:
        self.calls.append(("insert_configuration_module", request))
        self.insert_requests.append(request)
        return await super().insert_configuration_module(request, level=level)
