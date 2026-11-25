"""Each state resolves the names of its patches in its own enterprise.

Edge 101 is in enterprise 1, where Guest is segment 1 and Corp is 2. Edge 202
is in enterprise 2, where Voice is segment 1 and Guest is 2.
"""

import asyncio
import uuid
from collections.abc import Sequence
from typing import Any, override

import pytest
from pydantic import MISSING
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.enterprise.network_segment import NetworkSegmentData
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResult
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.client.schemas.enterprise import (
    GetEnterpriseNetworkSegments,
    NetworkSegment,
)
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.core.identity import ResolutionError
from pyvelo.testing import make_module_json
from pyvelo_intent import PatchError
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    desc,
    make_state,
)

ENTERPRISE_1 = [(0, "Global Segment"), (1, "Guest"), (2, "Corp")]
ENTERPRISE_2 = [(0, "Global Segment"), (1, "Voice"), (2, "Guest")]
TO_GUEST: dict[str, object] = {"100": {"segment_id": "Guest"}}


def device_settings(segments: list[tuple[int, str]]) -> dict[str, Any]:
    return {
        "lan": {
            "interfaces": [],
            "networks": [
                {"vlanId": 100, "name": "users", "segmentId": 0, "override": True}
            ],
        },
        "segments": [{"segment": desc(sid, name)} for sid, name in segments],
    }


def stack(
    edge_config_id: int, profile_config_id: int, segments: list[tuple[int, str]]
) -> GetEdgeConfigurationStackResult:
    """An edge configuration and its profile, each with one device settings module."""

    def rows(config_id: int) -> list[dict[str, Any]]:
        return [
            make_module_json(
                "deviceSettings",
                device_settings(segments),
                module_id=config_id + 1,
                configuration_id=config_id,
                version="e1",
            )
        ]

    return (
        EdgeConfiguration.model_validate(
            {
                "name": "Edge Specific Profile",
                "id": edge_config_id,
                "modules": rows(edge_config_id),
            }
        ),
        EnterpriseConfigurationProfile.model_validate(
            {
                "name": f"Profile {profile_config_id}",
                "id": profile_config_id,
                "modules": rows(profile_config_id),
            }
        ),
    )


def segment_row(object_id: int, name: str, segment_id: int) -> NetworkSegment:
    return NetworkSegment.model_construct(
        id_=object_id,
        name=name,
        logical_id=uuid.UUID(int=segment_id),
        type_=NetworkSegmentType.REGULAR,
        data=NetworkSegmentData.model_construct(segment_id=segment_id),
    )


class TwoEnterprises(MockVcoClient):
    """Serves edge 101 of enterprise 1 and edge 202 of enterprise 2.

    Each read yields to the event loop first, as a request does.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stacks: dict[int, GetEdgeConfigurationStackResult] = {
            101: stack(10, 20, ENTERPRISE_1),
            202: stack(30, 40, ENTERPRISE_2),
        }
        self.segments: dict[int, list[NetworkSegment]] = {
            1: [segment_row(1000 + s, n, s) for s, n in ENTERPRISE_1],
            2: [segment_row(2000 + s, n, s) for s, n in ENTERPRISE_2],
        }
        for one in self.stacks.values():
            _ = self.seed(*one)

    @override
    async def get_edge_configuration_stack(
        self, request: GetEdgeConfigurationStack
    ) -> GetEdgeConfigurationStackResult:
        await asyncio.sleep(0)
        return self.stacks[request.edge_id]

    @override
    async def get_enterprise_network_segments(
        self, request: GetEnterpriseNetworkSegments
    ) -> Sequence[NetworkSegment]:
        await asyncio.sleep(0)
        assert request.enterprise_id is not MISSING
        return self.segments[request.enterprise_id]


def vlan_segment(state: ConfigState) -> int:
    return state.edge.device_settings.lan.networks[0].segment_id


async def to_guest(pipeline: ConfigPipeline, state: ConfigState) -> None:
    _ = await pipeline.apply(
        state, module="device_settings", path="lan.networks", patch=TO_GUEST
    )


async def test_a_name_resolves_in_the_enterprise_of_its_state():
    client = TwoEnterprises()
    pipeline = ConfigPipeline(client)
    first = await pipeline.load(101, enterprise_id=1)
    second = await pipeline.load(202, enterprise_id=2)

    await to_guest(pipeline, first)
    await to_guest(pipeline, second)

    assert (vlan_segment(first), vlan_segment(second)) == (1, 2)
    _ = await pipeline.save(first)
    stored = client.store.rows[11]["data"]["lan"]["networks"][0]
    assert stored["segmentId"] == 1


async def test_states_loaded_at_once_each_resolve_in_their_own_enterprise():
    pipeline = ConfigPipeline(TwoEnterprises())

    first, second = await asyncio.gather(
        pipeline.load(101, enterprise_id=1), pipeline.load(202, enterprise_id=2)
    )
    await to_guest(pipeline, first)
    await to_guest(pipeline, second)

    assert (vlan_segment(first), vlan_segment(second)) == (1, 2)


async def test_each_state_has_its_own_copy_of_the_pipelines_resolvers():
    client = TwoEnterprises()
    registered = SegmentResolver(client)
    registry = ResolverRegistry()
    registry.register(registered)
    pipeline = ConfigPipeline(client, resolvers=registry)

    first = await pipeline.load(101, enterprise_id=1)
    second = await pipeline.load(202, enterprise_id=2)

    assert first.resolvers is not second.resolvers
    assert first.resolvers is not pipeline.resolvers
    own = first.resolvers.get_resolver(SegmentResolver)
    assert own is not registered
    assert own is not second.resolvers.get_resolver(SegmentResolver)
    assert sorted(i.name for i in own.identities()) == [
        "Corp",
        "Global Segment",
        "Guest",
    ]
    assert list(registered.identities()) == []  # the pipeline's stays unloaded
    assert first.resolvers.current_context is first.resolver_context


async def test_a_state_built_by_hand_resolves_only_what_it_is_given():
    state = make_state()
    patch: dict[str, object] = {"segments": {"Nope": {"outbound": {}}}}
    pipeline = ConfigPipeline(MockVcoClient())
    with pytest.raises(LookupError):
        _ = state.resolvers.get_resolver(SegmentResolver)
    with pytest.raises(PatchError, match="no item has the key"):
        _ = await pipeline.apply(state, module="firewall", patch=patch)

    await pipeline.load_resolvers(state)

    own = state.resolvers.get_resolver(SegmentResolver)
    assert [i.name for i in own.identities()] == ["Global Segment"]
    assert state.resolvers.current_context is state.resolver_context
    with pytest.raises(ResolutionError, match="Nope"):
        _ = await pipeline.apply(state, module="firewall", patch=patch)
