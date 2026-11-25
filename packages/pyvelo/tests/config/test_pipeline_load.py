"""Tests for ConfigPipeline.load() and load_profile()."""

import copy
import uuid
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResult
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.client.schemas.enterprise import (
    GetEnterpriseEdgesItem,
    GetEnterpriseNetworkSegments,
    GetEnterpriseServices,
)
from pyvelo.config import add
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolutionError, ResolverRegistry
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.state import ConfigState
from pyvelo.core.layers import Profile
from pyvelo.testing import MockVcoClient, make_firewall_module, make_module_json


def _make_stack() -> GetEdgeConfigurationStackResult:
    """Create a mock edge configuration stack (edge_config, profile_config)."""
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=10,
        modules=[make_firewall_module(mod_id=1, config_id=10)],
    )
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=20,
        modules=[make_firewall_module(mod_id=2, config_id=20, layer=Profile)],
    )
    return (edge_config, profile_config)


@pytest.mark.asyncio
async def test_load_by_edge_id():
    """Load by numeric edge ID should fetch config stack."""
    client = MockVcoClient(stack=_make_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42, enterprise_id=1)

    assert isinstance(state, ConfigState)
    assert state.target_type == "edge"
    assert state.edge_config is not None
    assert state.edge_config.name == "Edge Specific Profile"
    assert state.profile_config.name == "Default Profile"
    assert state.resolver_context.edge_id == 42
    assert state.resolver_context.enterprise_id == 1
    assert state.plan().target == "edge 42, enterprise 1, configuration 10"

    # Verify API call was made
    stack_calls = [c for c in client.calls if c[0] == "get_edge_configuration_stack"]
    assert len(stack_calls) == 1
    req = stack_calls[0][1]
    assert isinstance(req, GetEdgeConfigurationStack)
    assert req.edge_id == 42


@pytest.mark.asyncio
async def test_load_takes_snapshot():
    """Loading should take a snapshot for later diff detection."""
    client = MockVcoClient(stack=_make_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42)

    # Both loaded configurations should have public snapshots.
    assert isinstance(state.snapshot_for_target(), EdgeConfiguration)
    state.target_type = "profile"
    assert isinstance(state.snapshot_for_target(), EnterpriseConfigurationProfile)

    # No changes yet, so get_changed_modules should return empty
    assert state.get_changed_modules() == []


@pytest.mark.asyncio
async def test_load_by_edge_name():
    """Load by edge name should resolve name via EdgeResolver first."""
    client = MockVcoClient(
        stack=_make_stack(),
        edges=[
            GetEnterpriseEdgesItem.model_construct(
                name="Branch-NYC", id_=42, logical_id=uuid.UUID(int=0)
            )
        ],
    )

    # Set up edge resolver
    registry = ResolverRegistry()
    edge_resolver = EdgeResolver(client)
    registry.register(edge_resolver)

    pipeline = ConfigPipeline(client, resolvers=registry)
    state = await pipeline.load("Branch-NYC")

    assert state.resolver_context.edge_id == 42
    # The get_edge_configuration_stack call should have used edge_id=42
    stack_calls = [c for c in client.calls if c[0] == "get_edge_configuration_stack"]
    assert len(stack_calls) == 1
    req = stack_calls[0][1]
    assert isinstance(req, GetEdgeConfigurationStack)
    assert req.edge_id == 42


@pytest.mark.asyncio
async def test_load_by_name_is_strict():
    client = MockVcoClient(
        stack=_make_stack(),
        edges=[
            GetEnterpriseEdgesItem.model_construct(
                name="Branch-NYC", id_=42, logical_id=uuid.UUID(int=0)
            )
        ],
    )
    registry = ResolverRegistry()
    registry.register(EdgeResolver(client))
    pipeline = ConfigPipeline(client, resolvers=registry)
    with pytest.raises(ResolutionError) as exc:
        await pipeline.load("Branch-NY")
    assert exc.value.candidates == ["Branch-NYC"]
    assert not any(name == "get_edge_configuration_stack" for name, _ in client.calls)
    assert (await pipeline.load("branch-nyc")).target_type == "edge"


@pytest.mark.asyncio
async def test_load_profile_by_id():
    """load_profile by numeric ID should fetch profile config."""
    client = MockVcoClient(stack=_make_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load_profile(20, enterprise_id=1)

    assert state.target_type == "profile"
    assert state.edge_config is None
    assert state.profile_config.name == "Default Profile"
    assert state.resolver_context.enterprise_id == 1
    assert state.plan().target == (
        'profile "Default Profile", enterprise 1, configuration 20'
    )


@pytest.mark.asyncio
async def test_load_profile_refuses_an_edge_specific_configuration():
    client = MockVcoClient(stack=_make_stack())  # its store holds 10 and 20
    pipeline = ConfigPipeline(client)

    with pytest.raises(ValueError) as exc:
        _ = await pipeline.load_profile(10)

    assert str(exc.value) == (
        "configuration 10 is the edge-specific configuration of an edge, not a "
        "profile. Load that edge with load(), by its ID or name; to edit its "
        "profile, give load_profile the profile's ID"
    )
    assert [name for name, _ in client.calls] == ["get_configuration"]
    assert (await pipeline.load_profile(20)).target_type == "profile"


def _rule(name: str, **extra: object) -> dict[str, Any]:
    return {"name": name, "match": {}, "action": {"allow_or_deny": "allow"}, **extra}


_SEGMENT: dict[str, object] = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
}
_FIREWALL: dict[str, Any] = {
    "firewall_enabled": True,
    "segments": [
        {
            "segment": _SEGMENT,
            "outbound": [_rule("allow-web", ruleLogicalId="read-id-0000000000001")],
        }
    ],
}


def _raw_stack() -> GetEdgeConfigurationStackResult:
    """A stack whose modules were read from JSON: edge 10 (module 11), profile 20 (module 21)."""

    def row(module_id: int, config_id: int) -> dict[str, Any]:
        return make_module_json(
            "firewall",
            copy.deepcopy(_FIREWALL),
            module_id=module_id,
            configuration_id=config_id,
        )

    edge = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "id": 10, "modules": [row(11, 10)]}
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {"name": "Profile 20", "id": 20, "modules": [row(21, 20)]}
    )
    return edge, profile


@pytest.mark.asyncio
async def test_two_loads_are_two_reads_and_a_second_load_sees_the_save():
    client = MockVcoClient(stack=_raw_stack())
    client.store.issue_rule_ids = True
    pipeline = ConfigPipeline(client)
    state = await pipeline.load(42, enterprise_id=1)
    other = await pipeline.load(42, enterprise_id=1)
    assert other.edge_config is not state.edge_config
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment="Global Segment",
        path="outbound",
        patch={
            "block-ssh": add(
                {"action": {"allow_or_deny": "deny"}, "match": {}}, first=True
            )
        },
    )
    # An edit to one state is not in another until it is saved and loaded.
    assert [r.name for r in other.edge.firewall.segments[0].outbound] == ["allow-web"]
    result = await pipeline.save(state)
    assert [
        str(c.path) for c in result.server_changes[ConfigurationModuleName.FIREWALL]
    ] == ['segments["Global Segment"].outbound[block-ssh].ruleLogicalId']
    again = await pipeline.load(42, enterprise_id=1)
    rules = again.edge.firewall.segments[0].outbound
    assert [(r.name, r.rule_logical_id) for r in rules] == [
        ("block-ssh", "rule-id-00000000000001"),
        ("allow-web", "read-id-0000000000001"),
    ]
    assert again.plan().changed == ()
    methods = [method for method, _ in client.store.requests]
    assert methods.count("edge/getEdgeConfigurationStack") == 3


@pytest.mark.asyncio
async def test_a_client_seeded_from_a_state_serves_that_edges_stack():
    first = await ConfigPipeline(MockVcoClient(stack=_raw_stack())).load(42)
    client = MockVcoClient.for_state(first)
    loaded = await ConfigPipeline(client).load(42)
    assert loaded.edge_config is not first.edge_config
    assert loaded.edge.firewall.firewall_enabled.local is True
    assert loaded.profile_config.id_ == 20


@pytest.mark.asyncio
async def test_load_by_name_without_a_resolver_says_what_to_register():
    client = MockVcoClient(stack=_make_stack())
    with pytest.raises(LookupError) as exc:
        _ = await ConfigPipeline(client).load("branch-nyc", enterprise_id=1)
    assert str(exc.value) == (
        "load('branch-nyc') was given a name, and this pipeline has no edge resolver "
        "to turn it into an ID. Build the pipeline with build_default_pipeline(client), "
        "or register EdgeResolver(client) on pipeline.resolvers, or pass the edge's "
        "numeric ID."
    )
    with pytest.raises(LookupError) as exc:
        _ = await ConfigPipeline(client).load_profile("P", enterprise_id=1)
    assert str(exc.value) == (
        "load_profile('P') was given a name, and this pipeline has no profile resolver "
        "to turn it into an ID. Build the pipeline with build_default_pipeline(client), "
        "or register ProfileResolver(client) on pipeline.resolvers, or pass the "
        "profile's numeric ID."
    )
    assert client.calls == []


class ServiceRow(BaseModel):
    """A row of ``enterprise/getEnterpriseServices`` as the store answers it."""

    model_config = ConfigDict(extra="allow")
    id: int
    logicalId: str
    name: str
    type: str


async def listed_services(client: MockVcoClient) -> list[tuple[int, str, str]]:
    """The ID, logical ID and name of each service the store lists, asked through the client."""
    rows = await client.portal_list(
        "enterprise/getEnterpriseServices", GetEnterpriseServices(), ServiceRow
    )
    return [(row.id, row.logicalId, row.name) for row in rows]


@pytest.mark.asyncio
async def test_the_mock_client_holds_the_services_it_adds_as_objects():
    client = MockVcoClient()
    logical_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    client.add_service(701, logical_id=logical_id, name="dns-a", type_="dnsService")
    assert client.store.objects[701] == {
        "id": 701,
        "logicalId": logical_id,
        "name": "dns-a",
        "type": "dnsService",
        "object": "NETWORK_SERVICE",
        "data": {},
    }
    assert await listed_services(client) == [(701, logical_id, "dns-a")]


@pytest.mark.asyncio
async def test_the_mock_client_holds_the_segment_it_answers_as_an_object():
    client = MockVcoClient()
    (segment,) = await client.get_enterprise_network_segments(
        GetEnterpriseNetworkSegments()
    )
    held = client.store.objects[segment.id_]
    assert held["object"] == "NETWORK_SEGMENT"
    assert held["logicalId"] == str(segment.logical_id)
    assert (held["name"], held["type"]) == (segment.name, segment.type_.value)
    # A segment is not a service: the services listing leaves it out.
    assert await listed_services(client) == []
