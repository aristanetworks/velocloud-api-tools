"""QoS through ``ConfigPipeline.apply`` and ``state.effective``.

Patches on inherited values start from the profile.
"""

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import MISSING
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config import PatchError
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.core import Effective, Origin, effective_dump
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import MockVcoClient, make_module_json
from shown_paths import reached, round_trips
from test_state_plan import desc  # pyright: ignore[reportImplicitRelativeImport]

if TYPE_CHECKING:
    from pyvelo.client.models.configuration import QosData


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
    """An edge state whose edge QoS data is *edge* (no row for None).

    Its profile's QoS data is *profile*.
    """
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


async def test_the_effective_qos_view_gives_each_value_its_origin() -> None:
    state = state_of({**EDGE_QOS, "qoe": QOE})
    view = state.effective.qos
    assert view.qoe.origin is Origin.EDGE
    assert view.service_rate_limit.origin is Origin.PROFILE
    assert view.service_rate_limit.value.value == 40
    # Computed on each access: an edit shows in the next view.
    state.edge.qos.qoe.inherit()
    assert state.effective.qos.qoe.origin is Origin.FACTORY


async def test_the_effective_qos_view_of_an_edge_without_a_row_creates_nothing() -> (
    None
):
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


async def test_a_disabled_rate_limit_is_local_and_null_inherits_it() -> None:
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


async def test_a_patch_into_the_cos_mapping_of_a_segment_the_edge_lacks_localizes_from_the_profile_segment() -> (  # noqa: E501 (a test's name)
    None
):
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


async def test_a_patch_naming_the_cos_mapping_flag_is_refused() -> None:
    state = state_of(EDGE_QOS)
    before = state.plan().render()
    with pytest.raises(PatchError, match="'override' is the flag") as caught:
        await apply(
            state,
            {"segments": {"Global Segment": {"cosMapping": {"override": True}}}},
        )
    message = str(caught.value)
    assert "set_local()" in message
    assert "inherit()" in message
    assert 'send {"segments": {"Global Segment": {"cosMapping": null}}}' in message
    assert state.plan().changed == ()
    assert state.plan().render() == before


async def test_an_edge_patch_of_defaults_is_refused() -> None:
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


async def test_a_failed_qos_patch_leaves_a_localized_value_inherited() -> None:
    state = state_of(EDGE_QOS)
    with pytest.raises(PatchError, match="qoe"):
        await apply(state, {"serviceRateLimit": {"enabled": False}, "qoe": "abc"})
    assert state.edge.qos.service_rate_limit.is_local is False
    assert state.edge.qos.service_rate_limit.stored is MISSING
    assert state.plan().changed == ()


def flag_off_edge() -> dict[str, Any]:
    """An edge whose Voice cosMapping is stored with its flag off.

    It has values of its own (the profile's realtime.high is 35, its bulk.high 15).
    """
    stored = cos_mapping(override=False, realtime_high=50)
    stored["bulk"]["high"]["value"] = 22
    return {
        "segments": [{"segment": desc(3, "Voice"), "rules": [], "cosMapping": stored}]
    }


async def test_a_patch_into_a_cos_mapping_stored_with_its_flag_off_changes_only_what_it_names() -> (  # noqa: E501 (a test's name)
    None
):
    edge = flag_off_edge()
    state = state_of(edge)
    voice = state.effective.qos.get_segment(name="Voice")
    assert voice.cos_mapping.origin is Origin.EDGE
    await apply(
        state,
        {
            "segments": {
                "Voice": {"cosMapping": {"transactional": {"high": {"value": 21}}}}
            }
        },
    )
    written = qos_plan(state).data["segments"][0]["cosMapping"]
    expected = copy.deepcopy(edge["segments"][0]["cosMapping"])
    expected["transactional"]["high"]["value"] = 21
    expected["override"] = True
    assert written == expected
    assert written["realtime"]["high"]["value"] == 50
    assert written["bulk"]["high"]["value"] == 22
    assert changed_paths(state) == [
        "segments[Voice].cosMapping.transactional.high.value",
        "segments[Voice].cosMapping.override",
    ]


async def test_the_keep_patch_of_the_flag_off_warning_leaves_the_values_as_read_with_the_flag_on() -> (  # noqa: E501 (a test's name)
    None
):
    edge = flag_off_edge()
    state = state_of(edge)
    (warning,) = [w for w in state.plan().warnings if w.code == "flag_off_in_use"]
    patch = {"segments": {"Voice": {"cos_mapping": {}}}}
    assert json.dumps(patch) in warning.message
    await apply(state, patch)
    expected = copy.deepcopy(edge["segments"][0]["cosMapping"])
    expected["override"] = True
    assert qos_plan(state).data["segments"][0]["cosMapping"] == expected
    assert changed_paths(state) == ["segments[Voice].cosMapping.override"]
    assert not [w for w in state.plan().warnings if w.code == "flag_off_in_use"]


def shown_patches(message: str) -> list[dict[str, Any]]:
    """Each patch *message* shows after the word ``send``, as it is parsed."""
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    at = message.find("send {")
    while at != -1:
        patch, end = decoder.raw_decode(message, at + len("send "))
        found.append(patch)
        at = message.find("send {", end)
    return found


def flag_off_warning(state: ConfigState) -> ValidationWarning:
    (warning,) = [w for w in state.plan().warnings if w.code == "flag_off_in_use"]
    return warning


def test_the_flag_off_warning_is_at_the_object_in_attribute_names() -> None:
    state = state_of(flag_off_edge())
    warning = flag_off_warning(state)
    assert isinstance(warning.path, Path)
    assert warning.path.parts == ("segments", Key("Voice"), "cos_mapping")
    assert (warning.module, str(warning.path), warning.segment) == (
        "business_policy",
        "segments[Voice].cos_mapping",
        None,
    )
    round_trips(warning.path)
    owned = state.edge.qos.segments[0].cos_mapping
    assert owned.is_local is False
    assert reached(state.edge.qos, warning.path) is owned.stored
    assert (
        "The edge stores cos_mapping of segment 'Voice' with override false or absent."
        in warning.message
    )
    assert shown_patches(warning.message) == [
        {"segments": {"Voice": {"cos_mapping": None}}},
        {"segments": {"Voice": {"cos_mapping": {}}}},
    ]


async def test_the_inherit_patch_of_the_flag_off_warning_removes_the_object() -> None:
    state = state_of(flag_off_edge())
    message = flag_off_warning(state).message
    inherit, _ = shown_patches(message)
    assert inherit == {"segments": {"Voice": {"cos_mapping": None}}}
    assert f"To inherit the profile's, send {json.dumps(inherit)}" in message
    await apply(state, inherit)
    assert "cosMapping" not in qos_plan(state).data["segments"][0]
    assert changed_paths(state) == ["segments[Voice].cosMapping"]
    voice = state.effective.qos.get_segment(name="Voice")
    assert voice.cos_mapping.origin is Origin.PROFILE
    assert not [w for w in state.plan().warnings if w.code == "flag_off_in_use"]
