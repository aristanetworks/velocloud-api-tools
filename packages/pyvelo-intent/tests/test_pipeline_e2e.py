"""End-to-end integration test: load → apply → save.

Proves that the full pipeline works from loading a config stack through
merge, override flags, template fill, to saving with correct API calls.
"""

import uuid

import pytest
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResult
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.enterprise import GetEnterpriseEdgesItem
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo_intent.config import ConfigPipeline, SaveResult
from pyvelo_intent.config.firewall import (
    FirewallOutboundRule,
    OutboundRuleAction,
    OutboundRuleMatch,
)
from pyvelo_intent.testing import (
    MockVcoClient,
    make_firewall_module,
    make_firewall_segment,
)

# ---------------------------------------------------------------------------
# Stack builder
# ---------------------------------------------------------------------------

BRANCH_NYC = GetEnterpriseEdgesItem.model_construct(
    name="Branch-NYC", id_=42, logical_id=uuid.UUID(int=0)
)


def _make_realistic_stack() -> GetEdgeConfigurationStackResult:
    """Build a realistic config stack with edge and profile configs."""
    profile_rule = FirewallOutboundRule(
        name="allow-all",
        action=OutboundRuleAction(allow_or_deny="allow"),
        match=OutboundRuleMatch(),
    )
    profile_segments = [make_firewall_segment("Global Segment", 0, [profile_rule])]
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=200,
        modules=[
            make_firewall_module(mod_id=20, config_id=200, segments=profile_segments)
        ],
    )

    edge_segments = [make_firewall_segment("Global Segment", 0, [profile_rule])]
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=100,
        modules=[
            make_firewall_module(mod_id=10, config_id=100, segments=edge_segments)
        ],
    )

    return (edge_config, profile_config)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_e2e_load_apply_save():
    """Exercise loading a named edge, applying a firewall patch, and saving."""
    client = MockVcoClient(stack=_make_realistic_stack(), edges=[BRANCH_NYC])

    # Set up resolver registry with edge resolver
    registry = ResolverRegistry()
    edge_resolver = EdgeResolver(client)
    registry.register(edge_resolver)

    pipeline = ConfigPipeline(client, resolvers=registry)

    # 1. Load edge configuration by name
    state = await pipeline.load("Branch-NYC", enterprise_id=1)

    assert state.target_type == "edge"
    assert state.edge_config is not None
    assert state.edge_config.name == "Edge Specific Profile"

    # Verify the edge has the expected segment
    fw_data = state.edge_config.firewall.data
    seg = fw_data.get_segment(name="Global Segment")
    assert len(seg.outbound) == 1  # just the "allow-all" template rule

    # 2. Apply a firewall patch: add a block-ssh rule
    state = await pipeline.apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block-ssh": {
                            "name": "block-ssh",
                            "action": {"allow_or_deny": "deny"},
                            "match": {
                                "proto": 6,
                                "dport_low": 22,
                                "dport_high": 22,
                            },
                        }
                    }
                }
            }
        },
    )

    # Verify the rule was added
    assert state.edge_config is not None
    seg = state.edge_config.firewall.data.get_segment(name="Global Segment")
    assert len(seg.outbound) == 2
    rule_names = [r.name for r in seg.outbound]
    assert "allow-all" in rule_names
    assert "block-ssh" in rule_names

    # Verify override flag was set
    assert seg.outbound_override is True

    # Verify the new rule has the correct properties
    block_ssh = next(r for r in seg.outbound if r.name == "block-ssh")
    assert block_ssh.action.allow_or_deny == "deny"
    assert block_ssh.match.proto == 6
    assert block_ssh.match.destination_port_low == 22

    # 3. Save the changes
    result = await pipeline.save(state)

    assert isinstance(result, SaveResult)
    assert "firewall" in result.modules_updated
    assert result.modules_inserted == []

    # Verify the API call was made with the correct module ID
    assert len(client.update_requests) == 1
    update_req = client.update_requests[0]
    assert update_req.configuration_module_id == 10  # edge firewall module ID


@pytest.mark.asyncio
async def test_e2e_dry_run():
    """dry_run should report what would change without making API calls."""
    client = MockVcoClient(stack=_make_realistic_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42)

    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    result = await pipeline.save(state, dry_run=True)

    assert "firewall" in result.modules_updated
    # No update_configuration_module calls should have been made
    assert all(name != "update_configuration_module" for name, _ in client.calls)


@pytest.mark.asyncio
async def test_e2e_no_changes_skips_save():
    """If no changes were made, save should skip API calls."""
    client = MockVcoClient(stack=_make_realistic_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42)

    # Don't apply any changes
    result = await pipeline.save(state)

    assert result.modules_updated == []
    assert result.modules_inserted == []
    assert all(name != "update_configuration_module" for name, _ in client.calls)


@pytest.mark.asyncio
async def test_e2e_multiple_patches():
    """Multiple apply calls should accumulate changes."""
    client = MockVcoClient(stack=_make_realistic_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42)

    # First patch: disable firewall
    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    # Second patch: add a rule
    state = await pipeline.apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "new-rule": {
                            "name": "new-rule",
                            "action": {"allow_or_deny": "deny"},
                            "match": {"proto": 17},
                        }
                    }
                }
            }
        },
    )

    # Both changes should be present
    assert state.edge_config is not None
    assert state.edge_config.firewall.data.firewall_enabled is False
    seg = state.edge_config.firewall.data.get_segment(name="Global Segment")
    assert any(r.name == "new-rule" for r in seg.outbound)

    # Save should capture all changes
    result = await pipeline.save(state)
    assert "firewall" in result.modules_updated
