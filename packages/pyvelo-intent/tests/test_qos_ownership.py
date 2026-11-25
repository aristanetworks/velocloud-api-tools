"""QoS through ``ConfigPipeline.apply`` and ``state.effective``: patches on inherited values start from the profile."""

import copy
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.models.configuration import QosData
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.core import Effective, Origin, effective_dump
from pyvelo.testing import make_module_json
from pyvelo_intent import PatchError
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import desc  # pyright: ignore[reportImplicitRelativeImport]


def cos_mapping(*, override: bool = True, realtime_high: int = 35) -> dict[str, Any]:
    def level(high: int, normal: int, low: int) -> dict[str, Any]:
        return {
            "high": {"value": high, "ratelimit": False},
            "normal": {"value": normal, "ratelimit": False},
            "low": {"value": low, "ratelimit": False},
        }

    return {
        "transactional": level(20, 7, 1),
        "realtime": level(realtime_high, 15, 1),
        "bulk": level(15, 5, 1),
        "lsInputType": "weight",
        "override": override,
    }


RATE_LIMIT: dict[str, Any] = {"enabled": True, "inputType": "percent", "value": 40}
QOE: dict[str, Any] = {
    "voice": {"latency": {"yellow": 30, "red": 70}},
    "video": {"latency": {"yellow": 30, "red": 70}},
    "trans": {"latency": {"yellow": 55, "red": 85}},
}

PROFILE_QOS: dict[str, Any] = {
    "segments": [
        {"segment": desc(0, "Global Segment"), "rules": [], "defaults": []},
        {
            "segment": desc(3, "Voice"),
            "rules": [],
            "defaults": [],
            "cosMapping": cos_mapping(realtime_high=35),
        },
    ],
    "serviceRateLimit": RATE_LIMIT,
}
EDGE_QOS: dict[str, Any] = {
    "segments": [{"segment": desc(0, "Global Segment"), "rules": []}]
}


def state_of(
    edge: dict[str, Any] | None, profile: dict[str, Any] | None = None
) -> ConfigState:
    """An edge state whose edge QoS data is *edge* (no row for None), over a profile whose is *profile*."""
    edge_rows = (
        []
        if edge is None
        else [
            make_module_json(
                "QOS",
                copy.deepcopy(edge),
                module_id=1,
                configuration_id=10,
                version="e1",
            )
        ]
    )
    state = ConfigState(
        edge_config=EdgeConfiguration.model_validate(
            {"name": "Edge Specific Profile", "id": 10, "modules": edge_rows}
        ),
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {
                "name": "P",
                "id": 20,
                "modules": [
                    make_module_json(
                        "QOS",
                        copy.deepcopy(PROFILE_QOS if profile is None else profile),
                        module_id=2,
                        configuration_id=20,
                    )
                ],
            }
        ),
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=5),
    )
    state.take_snapshot()
    return state


def qos_plan(state: ConfigState) -> ModulePlan:
    (plan,) = state.plan().changed
    assert plan.name == "QOS"
    return plan


def changed_paths(state: ConfigState) -> list[str]:
    return [str(c.path) for c in qos_plan(state).changes]


async def apply(state: ConfigState, patch: dict[str, Any]) -> None:
    _ = await ConfigPipeline(MockVcoClient.for_state(state)).apply(
        state, module="QOS", patch=patch
    )


# --- the effective view -----------------------------------------------------


async def test_the_effective_qos_view_gives_each_value_its_origin():
    state = state_of({**EDGE_QOS, "qoe": QOE})
    view = state.effective.qos
    assert view.qoe.origin is Origin.EDGE
    assert view.service_rate_limit.origin is Origin.PROFILE
    assert view.service_rate_limit.value.value == 40
    assert view.qos_client_connector.origin is Origin.FACTORY
    assert view.qos_client_connector.value is None
    # Computed on each access: an edit shows in the next view.
    state.edge.qos.qoe.inherit()
    assert state.effective.qos.qoe.origin is Origin.FACTORY


async def test_the_effective_qos_view_of_an_edge_without_a_row_creates_nothing():
    state = state_of(None)
    before = state.plan().render()
    modules_before = state.edge_config.modules if state.edge_config else None
    view: QosData[Effective] = state.effective.qos
    origins = {r.origin for r in effective_dump(view)[1].values()}
    assert origins <= {Origin.PROFILE, Origin.FACTORY}
    assert view.service_rate_limit.origin is Origin.PROFILE
    assert view.qoe.origin is Origin.FACTORY
    assert state.find_module("QOS") is None
    assert state.edge_config is not None
    assert state.edge_config.modules == modules_before
    assert state.plan().changed == ()
    assert state.plan().render() == before


# --- a patch on an inherited value starts from the profile ------------------


async def test_a_disabled_rate_limit_is_local_and_null_inherits_it():
    state = state_of(EDGE_QOS)
    await apply(state, {"serviceRateLimit": {"enabled": False}})
    assert changed_paths(state) == ["serviceRateLimit"]
    # The whole object is the edge's own: the profile's other keys, enabled off.
    assert qos_plan(state).data["serviceRateLimit"] == {**RATE_LIMIT, "enabled": False}
    assert state.edge.qos.service_rate_limit.is_local is True
    assert state.effective.qos.service_rate_limit.origin is Origin.EDGE
    assert state.effective.qos.service_rate_limit.value.enabled is False
    # The profile is as it was.
    assert state.profile.qos.service_rate_limit.enabled is True

    stored = state_of(
        {**EDGE_QOS, "serviceRateLimit": {**RATE_LIMIT, "enabled": False}}
    )
    await apply(stored, {"serviceRateLimit": None})
    plan = qos_plan(stored)
    assert [str(c.path) for c in plan.changes] == ["serviceRateLimit"]
    assert "serviceRateLimit" not in plan.data
    assert stored.effective.qos.service_rate_limit.origin is Origin.PROFILE


async def test_a_patch_into_the_cos_mapping_of_a_segment_the_edge_lacks_localizes_from_the_profile_segment():
    state = state_of(EDGE_QOS)
    await apply(
        state,
        {"segments": {"Voice": {"cosMapping": {"realtime": {"high": {"value": 40}}}}}},
    )
    plan = state.plan()
    (module,) = plan.changed
    # The change is the caller's, inside the placeholder; as for the firewall,
    # the segment the edge lacked as read is also listed as reconciled.
    assert [str(c.path) for c in module.changes] == ["segments[Voice].cosMapping"]
    assert module.reconciled_segments == ("Voice",)
    # The placeholder is the profile's descriptor and the one local value; the
    # profile's own `defaults` are not copied.
    assert module.data["segments"] == [
        {"segment": desc(0, "Global Segment"), "rules": []},
        {
            "segment": desc(3, "Voice"),
            "rules": [],
            "cosMapping": cos_mapping(override=True, realtime_high=40),
        },
    ]
    assert set(module.data) == {"segments"}
    # The profile is as it was.
    voice = state.profile.qos.get_segment(name="Voice")
    assert voice.cos_mapping.realtime.high.value == 35


async def test_a_patch_naming_the_cos_mapping_flag_is_refused():
    state = state_of(EDGE_QOS)
    before = state.plan().render()
    with pytest.raises(PatchError, match="'override' is the flag") as caught:
        await apply(
            state,
            {"segments": {"Global Segment": {"cosMapping": {"override": True}}}},
        )
    assert "send null for the field to inherit" in str(caught.value)
    assert state.plan().changed == ()
    assert state.plan().render() == before


async def test_an_edge_patch_of_defaults_is_refused():
    state = state_of(EDGE_QOS)
    before = state.plan().render()
    patch: dict[str, Any] = {
        "segments": {"Global Segment": {"defaults": {"Default": {"action": {}}}}}
    }
    with pytest.raises(PatchError) as caught:
        await apply(state, patch)
    message = str(caught.value)
    assert "'defaults' belongs to the profile" in message
    assert "added to the profile's" in message
    assert "Send this patch to the profile's state" in message
    assert str(caught.value.path) == 'segments["Global Segment"].defaults'
    assert state.plan().changed == ()
    assert state.plan().render() == before


async def test_a_failed_qos_patch_leaves_a_localized_value_inherited():
    state = state_of(EDGE_QOS)
    with pytest.raises(PatchError, match="qoe"):
        await apply(state, {"serviceRateLimit": {"enabled": False}, "qoe": "abc"})
    assert state.edge.qos.service_rate_limit.is_local is False
    assert state.edge.qos.service_rate_limit.stored is MISSING
    assert state.plan().changed == ()
