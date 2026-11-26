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
from pyvelo.config import PatchError
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.state import ConfigState
from pyvelo.core.identity import (
    NetworkServiceIdentity,
    ResolutionError,
    SegmentIdentity,
)
from pyvelo.testing import MockVcoClient, make_module_json
from test_save_refs import (  # pyright: ignore[reportImplicitRelativeImport]
    SERVICES,
    loaded_profile,
    profile_state,
    served,
)
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


async def test_a_name_resolves_in_the_enterprise_of_its_state() -> None:
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


async def test_states_loaded_at_once_each_resolve_in_their_own_enterprise() -> None:
    pipeline = ConfigPipeline(TwoEnterprises())

    first, second = await asyncio.gather(
        pipeline.load(101, enterprise_id=1), pipeline.load(202, enterprise_id=2)
    )
    await to_guest(pipeline, first)
    await to_guest(pipeline, second)

    assert (vlan_segment(first), vlan_segment(second)) == (1, 2)


async def test_each_state_has_its_own_copy_of_the_pipelines_resolvers() -> None:
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


async def test_a_state_built_by_hand_resolves_only_what_it_is_given() -> None:
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


async def test_load_resolvers_with_an_identity_loads_only_that_resolver() -> None:
    pipeline, client, state = await loaded_profile()
    segments = state.resolvers.get_resolver(SegmentResolver)
    state.resolvers.set_context(ResolverContext(enterprise_id=99))
    calls = len(client.calls)

    await pipeline.load_resolvers(state, NetworkServiceIdentity)

    services = state.resolvers.get_resolver(NetworkServiceResolver)
    assert sorted(i.name for i in services.identities()) == ["dns-a", "dns-b"]
    assert [m for m, _ in client.store.requests] == [SERVICES]
    assert len(client.calls) == calls  # the segments were not read again
    assert state.resolvers.get_resolver(SegmentResolver) is segments
    assert state.resolvers.current_context is state.resolver_context


async def test_load_resolvers_with_an_identity_the_state_lacks_registers_it() -> None:
    client = served(profile_state())
    pipeline = ConfigPipeline(client)
    registered = NetworkServiceResolver(client)
    pipeline.resolvers.register(registered)
    state = profile_state()
    with pytest.raises(LookupError):
        _ = state.resolvers.get_resolver(NetworkServiceResolver)

    await pipeline.load_resolvers(state, NetworkServiceIdentity)

    own = state.resolvers.get_resolver(NetworkServiceResolver)
    assert own is not registered  # an unloaded copy of the pipeline's
    assert own.loaded
    assert not registered.loaded
    assert state.resolvers.current_context is state.resolver_context

    # A pipeline with none of its own gives the state a new one.
    other = profile_state()
    await ConfigPipeline(client).load_resolvers(other, NetworkServiceIdentity)
    assert other.resolvers.get_resolver(NetworkServiceResolver).loaded


async def test_load_resolvers_with_no_identity_leaves_the_services_unloaded() -> None:
    client = served(profile_state())
    state = profile_state()

    await ConfigPipeline(client).load_resolvers(state)

    assert not state.resolvers.get_resolver(NetworkServiceResolver).loaded
    assert SERVICES not in [m for m, _ in client.store.requests]


async def test_load_resolvers_with_again_reloads_a_loaded_resolver() -> None:
    pipeline, client, state = await loaded_profile()
    segments = state.resolvers.get_resolver(SegmentResolver)
    calls = len(client.calls)

    await pipeline.load_resolvers(state, SegmentIdentity)
    assert len(client.calls) == calls  # nothing read

    await pipeline.load_resolvers(state, SegmentIdentity, again=True)

    assert state.resolvers.get_resolver(SegmentResolver) is segments
    assert len(client.calls) == calls + 1  # the segments were read again
    assert [m for m, _ in client.store.requests] == []  # and nothing else


async def test_the_segment_resolver_of_a_loaded_state_says_it_is_loaded() -> None:
    _, _, state = await loaded_profile()

    assert state.resolvers.get_resolver(SegmentResolver).loaded
    assert not state.resolvers.get_resolver(NetworkServiceResolver).loaded


async def test_load_resolvers_loads_a_type_once_unless_asked_again() -> None:
    pipeline, client, state = await loaded_profile()

    await pipeline.load_resolvers(state, NetworkServiceIdentity)
    await pipeline.load_resolvers(state, NetworkServiceIdentity)
    assert [m for m, _ in client.store.requests] == [SERVICES]

    client.add_service(
        703, logical_id=str(uuid.UUID(int=703)), name="dns-c", type_="dns"
    )
    await pipeline.load_resolvers(state, NetworkServiceIdentity, again=True)
    assert [m for m, _ in client.store.requests] == [SERVICES, SERVICES]
    services = state.resolvers.get_resolver(NetworkServiceResolver)
    assert sorted(i.name for i in services.identities()) == ["dns-a", "dns-b", "dns-c"]


async def test_load_resolvers_copies_the_pipelines_resolver_for_a_type_the_state_lacks() -> (  # noqa: E501 (a test's name)
    None
):
    client = served(profile_state())
    pipeline = ConfigPipeline(client)
    registered = SegmentResolver(client)
    pipeline.resolvers.register(registered)
    state = profile_state()

    await pipeline.load_resolvers(state, SegmentIdentity)

    own = state.resolvers.get_resolver(SegmentResolver)
    assert own is not registered
    assert [i.name for i in own.identities()] == ["Global Segment"]
    assert list(registered.identities()) == []


async def test_load_resolvers_refuses_a_type_neither_the_state_nor_the_pipeline_has() -> (  # noqa: E501 (a test's name)
    None
):
    state = profile_state()
    pipeline = ConfigPipeline(MockVcoClient())

    with pytest.raises(ValueError, match="has a resolver for SegmentIdentity") as error:
        await pipeline.load_resolvers(state, SegmentIdentity)

    message = str(error.value)
    assert "SegmentIdentity" in message
    assert "pipeline.resolvers" in message
    assert "state.resolvers" in message
