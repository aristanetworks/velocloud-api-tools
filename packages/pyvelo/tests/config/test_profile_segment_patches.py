"""``ConfigPipeline.apply`` on a profile: an ``add`` of a segment assigns it.

The ``add`` is under ``segments`` of any of the three modules, and it assigns the
segment as ``assign_segment`` does.

Each state is built by ``profile_state`` (a profile target, a segment resolver
loaded with Global, Guest and Voice, the profile holding the Global Segment
unless a test says otherwise). A client that sends one patch per module, in any
order, each with the body it wants, never needs to know which call assigned the
segment.
"""

import copy
import itertools
import uuid
from collections.abc import Mapping
from typing import Any, cast

import pytest
from profile_state import (
    module_store_pipeline,
    profile_state,
)
from pyvelo.client.models.configuration.profile_segments import (
    AddedSegment,
    default_rules,
    new_segment_entry,
)
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.config.state import ConfigState
from pyvelo.core import PatchError, Profile, add, delete
from pyvelo.core.identity import NetworkServiceIdentity, ResolutionError
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import make_network_service_resolver
from segment_helpers import (
    GLOBAL,
    GUEST,
    descriptor,
    ds_entry,
    firewall_entry,
    identity,
    qos_entry,
)

DS = "deviceSettings"
QOS = "QOS"


def qos_of(state: ConfigState) -> QosConfigurationModule[Profile]:
    module = state.module(QOS)
    assert isinstance(module, QosConfigurationModule)
    return cast("QosConfigurationModule[Profile]", module)  # the state's profile layer


FIREWALL = "firewall"
MODULES = (DS, QOS, FIREWALL)

GUEST_AT = Path(("segments", Key("Guest")))


async def global_only() -> ConfigState:
    return await profile_state(
        [ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )


async def guest_everywhere() -> ConfigState:
    return await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )


async def patch(
    state: ConfigState, module: str, segments: object, **address: Any
) -> ConfigState:
    return await ConfigPipeline(client=None).apply(
        state, module=module, patch={"segments": segments}, **address
    )


def plan_of(state: ConfigState, module: str) -> ModulePlan:
    (planned,) = [m for m in state.plan().modules if m.name == module]
    return planned


def entry_of(planned: ModulePlan, name: str) -> dict[str, Any]:
    (entry,) = [s for s in planned.data["segments"] if s["segment"]["name"] == name]
    return entry


def names_in(state: ConfigState, module: str) -> list[str]:
    return [s["segment"]["name"] for s in plan_of(state, module).data["segments"]]


def without_rule_ids(entry: dict[str, Any]) -> dict[str, Any]:
    entry = copy.deepcopy(entry)
    for key in ("rules", "defaults"):
        for rule in entry.get(key) or []:
            rule.pop("ruleLogicalId", None)
    return entry


def rule(name: str) -> dict[str, Any]:
    """A QoS rule as the wire has it, from the first template rule renamed."""
    made = default_rules()[0]
    made["name"] = name
    made["ruleLogicalId"] = str(uuid.uuid4())
    return made


def kept(state: ConfigState, module: str, position: int = 0) -> dict[str, Any]:
    """The entry at *position* of *module* as read, as a list value can give it."""
    return copy.deepcopy(state.module(module).raw_data["segments"][position])  # pyright: ignore[reportIndexIssue, reportUnknownVariableType]


def dumps(state: ConfigState) -> list[object]:
    """The JSON of each of the three modules, to see that none changed."""
    return [
        state.module(name).model_dump(mode="json", by_alias=True) for name in MODULES
    ]


def added_entry(state: ConfigState, module: str) -> Mapping[str, Any]:
    """The raw entry an assignment made for Guest in *module*, as it recorded it."""
    (added,) = [
        a for a in state.module(module).additions if isinstance(a, AddedSegment)
    ]
    return added.entry


def assigned_defaults(state: ConfigState) -> list[dict[str, Any]]:
    """The ``defaults`` an assignment made for Guest in QoS, as it recorded them."""
    return added_entry(state, QOS)["defaults"]


# --- an add assigns, from any module, in any order -----------------------------


@pytest.mark.parametrize("order", list(itertools.permutations(MODULES)), ids="-".join)
async def test_adds_in_any_module_order_each_merge_their_own_body(
    order: tuple[str, ...],
) -> None:
    state = await global_only()
    the_rule = rule("Box")
    bodies: dict[str, dict[str, object]] = {
        QOS: {"rules": [the_rule]},
        DS: {"vpn": {"conditionalBackhaul": True}},
        FIREWALL: {"firewall_logging_enabled": True},
    }

    for module in order:
        _ = await patch(state, module, {"Guest": add(bodies[module])})

    plan = state.plan()
    assert [(m.name, m.operation) for m in plan.changed] == [
        (DS, "update"),
        (QOS, "update"),
        (FIREWALL, "update"),
    ]
    assert all(m.added_segments == ("Guest",) for m in plan.changed)
    assert plan.segment_warnings == ()
    template = {m: new_segment_entry(m, identity(GUEST)) for m in MODULES}
    # Each body is in its own module's entry and in no other.
    ds = entry_of(plan_of(state, DS), "Guest")
    assert ds["vpn"]["conditionalBackhaul"] is True
    assert {k: v for k, v in ds.items() if k != "vpn"} == template[DS]
    qos = entry_of(plan_of(state, QOS), "Guest")
    assert [r["name"] for r in qos["rules"]] == ["Box"]
    assert qos["rules"][0]["ruleLogicalId"] == the_rule["ruleLogicalId"]
    # The defaults are the template's, with the IDs the assignment made once.
    assert without_rule_ids({"defaults": qos["defaults"]}) == without_rule_ids(
        {"defaults": template[QOS]["defaults"]}
    )
    assert [d["ruleLogicalId"] for d in qos["defaults"]] == [
        d["ruleLogicalId"] for d in assigned_defaults(state)
    ]
    assert qos["cosMapping"] == template[QOS]["cosMapping"]
    assert qos["webProxy"] == template[QOS]["webProxy"]
    assert "vpn" not in qos
    assert "firewall_logging_enabled" not in qos
    firewall = entry_of(plan_of(state, FIREWALL), "Guest")
    assert firewall == {**template[FIREWALL], "firewall_logging_enabled": True}


async def test_assign_segment_after_an_add_raises_nothing_to_do() -> None:
    state = await global_only()
    _ = await patch(state, QOS, {"Guest": add({})})
    before = state.plan().render()

    with pytest.raises(ValueError, match="Guest is in all three modules already"):
        _ = await ConfigPipeline(client=None).assign_segment(state, "Guest")

    assert state.plan().render() == before


async def test_the_first_add_assigns_all_three_modules() -> None:
    state = await global_only()

    _ = await patch(state, FIREWALL, {"Guest": add({})})

    plan = state.plan()
    assert [m.name for m in plan.changed] == [DS, QOS, FIREWALL]
    for module in MODULES:
        assert names_in(state, module) == ["Global Segment", "Guest"]


async def test_an_add_in_another_case_assigns_the_enterprise_name() -> None:
    state = await global_only()

    _ = await patch(state, QOS, {"gUEST": add({})})

    assert plan_of(state, QOS).added_segments == ("Guest",)
    assert names_in(state, FIREWALL) == ["Global Segment", "Guest"]


async def test_an_addressed_add_assigns() -> None:
    state = await global_only()

    _ = await ConfigPipeline(client=None).apply(
        state, module="qos", segment="Guest", patch=add({})
    )

    assert [m.name for m in state.plan().changed] == [DS, QOS, FIREWALL]


async def test_a_plus_add_with_a_name_assigns() -> None:
    state = await global_only()

    _ = await patch(state, QOS, {"+": add({"segment": {"name": "guest"}})})

    assert [m.added_segments for m in state.plan().changed] == [("Guest",)] * 3
    assert names_in(state, QOS) == ["Global Segment", "Guest"]


async def test_a_plus_add_without_a_name_raises() -> None:
    state = await global_only()
    before = dumps(state)

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"+": add({"rules": []})})

    assert raised.value.kind == "segment_rule"
    assert raised.value.path == Path(("segments", Key("+")))
    assert '"segment": {"name": "Guest"}' in raised.value.message
    assert dumps(state) == before


async def test_an_add_of_a_segment_the_module_had_as_read_raises_exists() -> None:
    state = await guest_everywhere()

    for module, wire in (("qos", QOS), ("firewall", FIREWALL), ("device_settings", DS)):
        with pytest.raises(PatchError) as raised:
            _ = await patch(state, module, {"Guest": add({})})
        assert raised.value.kind == "exists"
        assert raised.value.path == GUEST_AT
        assert f"Guest is already in {wire}; change it with a plain object" in str(
            raised.value
        )
    assert state.plan().changed == ()


async def test_an_add_after_assign_segment_merges_into_the_entry() -> None:
    state = await global_only()
    pipeline = ConfigPipeline(client=None)
    _ = await pipeline.assign_segment(state, "Guest")
    made = [added_entry(state, name) for name in MODULES]

    _ = await pipeline.apply(
        state,
        module="qos",
        patch={"segments": {"Guest": add({"rules": [rule("Box")]})}},
    )

    qos = state.module(QOS)
    assert len(qos.additions) == 1
    assert added_entry(state, QOS) == made[1]
    guest = entry_of(plan_of(state, QOS), "Guest")
    assert [r["name"] for r in guest["rules"]] == ["Box"]
    assert [d["ruleLogicalId"] for d in guest["defaults"]] == [
        d["ruleLogicalId"] for d in made[1]["defaults"]
    ]
    assert [m.added_segments for m in state.plan().changed] == [("Guest",)] * 3


async def test_an_add_completes_a_partial_profile() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL)],
        [firewall_entry(GLOBAL)],
    )

    _ = await patch(state, FIREWALL, {"Guest": add({"firewall_logging_enabled": True})})

    plan = state.plan()
    assert [(m.name, m.added_segments) for m in plan.modules] == [
        (DS, ()),
        (QOS, ("Guest",)),
        (FIREWALL, ("Guest",)),
    ]
    assert entry_of(plan_of(state, FIREWALL), "Guest")["firewall_logging_enabled"]
    assert plan.segment_warnings == ()


async def test_an_add_to_a_module_that_has_it_completes_the_others_too() -> None:
    # Guest is in device settings and firewall as read; QoS lacks it. The add is
    # to QoS, so it is accepted, and the others have it already.
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )

    _ = await patch(state, QOS, {"Guest": add({"rules": [rule("Box")]})})

    assert [(m.name, m.added_segments) for m in state.plan().changed] == [
        (QOS, ("Guest",))
    ]


# --- the body's descriptor ---------------------------------------------------------


async def test_an_add_with_its_own_descriptor_is_taken() -> None:
    state = await global_only()
    body = new_segment_entry(QOS, identity(GUEST))
    for default in body["defaults"]:
        del default["ruleLogicalId"]

    _ = await patch(state, QOS, {"Guest": add(body)})

    qos = entry_of(plan_of(state, QOS), "Guest")
    assert qos["segment"] == descriptor(GUEST)
    assert [d["ruleLogicalId"] for d in qos["defaults"]] == [
        d["ruleLogicalId"] for d in assigned_defaults(state)
    ]
    assert [m.added_segments for m in state.plan().changed] == [("Guest",)] * 3


async def test_an_add_with_the_name_in_another_case_in_its_descriptor_is_taken() -> (
    None
):
    state = await global_only()

    _ = await patch(state, QOS, {"Guest": add({"segment": {"name": "GUEST"}})})

    assert names_in(state, QOS) == ["Global Segment", "Guest"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("segmentId", 7),
        ("type", "CDE"),
        ("segmentLogicalId", "00000000-0000-4000-8000-000000000001"),
        ("name", "Voice"),
    ],
)
async def test_an_add_with_another_descriptor_raises(key: str, value: object) -> None:
    state = await global_only()
    before = dumps(state)
    body = {"segment": {**descriptor(GUEST), key: value}}

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"Guest": add(body)})

    assert raised.value.kind == "segment_rule"
    assert raised.value.path == GUEST_AT.child("segment")
    assert "Guest" in raised.value.message
    assert dumps(state) == before
    assert state.plan().changed == ()


async def test_an_add_with_a_position_moves_the_new_entry() -> None:
    state = await global_only()

    _ = await patch(state, QOS, {"Guest": add({}, first=True)})

    assert names_in(state, QOS) == ["Guest", "Global Segment"]
    assert names_in(state, FIREWALL) == ["Global Segment", "Guest"]


# --- the name ------------------------------------------------------------------------


async def test_an_unknown_name_raises_resolution_error_and_changes_nothing() -> None:
    state = await global_only()
    before = dumps(state)
    rendered = state.plan().render()

    with pytest.raises(ResolutionError) as raised:
        _ = await patch(state, QOS, {"Gest": add({})})

    assert "Guest" in str(raised.value)
    assert dumps(state) == before
    assert state.plan().render() == rendered
    assert all(state.module(name).additions == () for name in MODULES)


async def test_no_segment_resolver_raises_patch_error_with_the_lookup_error_as_cause() -> (  # noqa: E501 (a test's name)
    None
):
    state = await global_only()
    state.resolvers = ResolverRegistry()

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"Guest": add({})})

    assert raised.value.kind == "invalid_value"
    assert raised.value.path == GUEST_AT
    assert isinstance(raised.value.__cause__, LookupError)
    assert "load_resolvers" in str(raised.value.__cause__)
    assert state.plan().changed == ()


# --- the modules ----------------------------------------------------------------------


async def test_a_profile_without_qos_refuses_an_add() -> None:
    state = await global_only()
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    modules.remove(qos_of(state))
    before = state.plan().render()

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, FIREWALL, {"Guest": add({})})

    assert raised.value.kind == "segment_rule"
    assert raised.value.path == Path(("segments",))
    assert "QOS" in raised.value.message
    assert state.plan().render() == before


@pytest.mark.parametrize(
    "form",
    [
        pytest.param("add", id="an add"),
        pytest.param("plus", id="an add under +"),
        pytest.param("addressed", id="an addressed add"),
        pytest.param("delete", id="a delete"),
        pytest.param("list", id="a list value"),
    ],
)
async def test_an_add_to_a_module_the_profile_lacks_raises_patch_error(
    form: str,
) -> None:
    state = await global_only()
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    modules.remove(qos_of(state))
    before = state.plan().render()
    call: dict[str, Any] = {
        "add": {"patch": {"segments": {"Guest": add({})}}},
        "plus": {"patch": {"segments": {"+": add({"segment": {"name": "Guest"}})}}},
        "addressed": {"segment": "Guest", "patch": add({})},
        "delete": {"patch": {"segments": {"Global Segment": delete()}}},
        "list": {"patch": {"segments": []}},
    }[form]

    with pytest.raises(PatchError) as raised:
        _ = await ConfigPipeline(client=None).apply(state, module="qos", **call)

    assert raised.value.kind == "segment_rule"
    assert raised.value.path == Path(("segments",))
    assert "no QOS module" in raised.value.message
    assert "not supported" in raised.value.message
    assert state.find_module(QOS) is None
    assert state.plan().render() == before


async def test_a_patch_without_segment_ops_to_a_module_the_profile_lacks_keeps_its_error() -> (  # noqa: E501 (a test's name)
    None
):
    state = await global_only()
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    modules.remove(qos_of(state))

    with pytest.raises(ValueError, match="not found") as raised:
        _ = await patch(state, QOS, {"Global Segment": {"rules": []}})

    assert not isinstance(raised.value, PatchError)


async def test_a_change_on_a_firewall_only_profile_still_works() -> None:
    state = await global_only()
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    modules[:] = [m for m in modules if m.name == FIREWALL]

    _ = await patch(
        state, FIREWALL, {"Global Segment": {"firewall_logging_enabled": True}}
    )

    assert [m.name for m in state.plan().changed] == [FIREWALL]


# --- a list value ---------------------------------------------------------------------


async def test_a_list_value_with_a_new_segment_assigns_it() -> None:
    state = await global_only()
    first = kept(state, QOS)

    _ = await patch(state, QOS, [first, {"segment": {"name": "guest"}, "rules": []}])

    plan = state.plan()
    assert [m.name for m in plan.changed] == [DS, QOS, FIREWALL]
    assert names_in(state, QOS) == ["Global Segment", "Guest"]
    template = new_segment_entry(QOS, identity(GUEST))
    guest = entry_of(plan_of(state, QOS), "Guest")
    assert guest["rules"] == []
    assert without_rule_ids({"defaults": guest["defaults"]}) == without_rule_ids(
        {"defaults": template["defaults"]}
    )
    assert guest["cosMapping"] == template["cosMapping"]
    assert guest["webProxy"] == template["webProxy"]
    assert [d["ruleLogicalId"] for d in guest["defaults"]] == [
        d["ruleLogicalId"] for d in assigned_defaults(state)
    ]
    assert entry_of(plan_of(state, DS), "Guest") == new_segment_entry(
        DS, identity(GUEST)
    )
    assert entry_of(plan_of(state, FIREWALL), "Guest") == new_segment_entry(
        FIREWALL, identity(GUEST)
    )


async def test_a_list_value_with_a_new_segment_of_another_descriptor_raises() -> None:
    state = await global_only()
    first = kept(state, QOS)
    before = dumps(state)

    with pytest.raises(PatchError) as raised:
        _ = await patch(
            state,
            QOS,
            [first, {"segment": {"name": "Guest", "segmentId": 7}, "rules": []}],
        )

    assert raised.value.kind == "segment_rule"
    assert dumps(state) == before


UNKNOWN_SERVICE = {"dns": {"primaryProvider": {"service": "dns-x"}}}


@pytest.mark.parametrize(
    ("form", "entry_at"),
    [("list value", Path(("segments", 1))), ("add", GUEST_AT)],
)
async def test_an_unknown_service_in_a_new_segment_entry_names_the_whole_path(
    form: str, entry_at: Path
) -> None:
    state = await global_only()
    state.resolvers.register(
        await make_network_service_resolver(
            [NetworkServiceIdentity("dns-a", uuid.UUID(int=1), 701, "dns")]
        )
    )
    segments: object = (
        [kept(state, DS), {"segment": {"name": "Guest"}, **UNKNOWN_SERVICE}]
        if form == "list value"
        else {"Guest": add(UNKNOWN_SERVICE)}
    )
    before = dumps(state)

    with pytest.raises(ResolutionError) as raised:
        _ = await patch(state, DS, segments)

    error = raised.value
    full = Path((*entry_at.parts, "dns", "primary_provider", "service"))
    assert error.path == full
    assert str(error).startswith(
        f"{full}: Unknown network service of type 'dns': 'dns-x'."
    )
    assert str(error).count("primary_provider") == 1
    assert (error.name, error.identity_type, error.candidates) == (
        "dns-x",
        NetworkServiceIdentity,
        ["dns-a"],
    )
    assert dumps(state) == before


# --- an entry copied from another profile ---------------------------------------------


def copied_guest_entry() -> dict[str, Any]:
    """A full QoS entry for Guest as another profile holds it: its own rule IDs."""
    return new_segment_entry(QOS, identity(GUEST))


def stored_default_ids(state: ConfigState) -> list[str]:
    guest = entry_of(plan_of(state, QOS), "Guest")
    return [d["ruleLogicalId"] for d in guest["defaults"]]


@pytest.mark.parametrize("key", ["ruleLogicalId", "rule_logical_id"])
async def test_an_add_of_a_copied_entry_takes_the_assignments_defaults_ids(
    key: str,
) -> None:
    state = await global_only()
    copied = copied_guest_entry()
    for item in copied["defaults"]:
        item[key] = item.pop("ruleLogicalId")

    _ = await patch(state, QOS, {"Guest": add(copied)})

    ids = [d["ruleLogicalId"] for d in assigned_defaults(state)]
    assert stored_default_ids(state) == ids
    assert len(set(ids)) == 4
    assert not set(ids) & {d[key] for d in copied["defaults"]}
    assert names_in(state, DS) == ["Global Segment", "Guest"]


async def test_an_add_of_a_copied_entry_keeps_its_rules_and_actions() -> None:
    state = await global_only()
    copied = copied_guest_entry()
    copied["rules"] = [rule("Voice")]
    copied["defaults"][0]["action"]["QoS"]["type"] = "transactional"

    _ = await patch(state, QOS, {"Guest": add(copied)})

    guest = entry_of(plan_of(state, QOS), "Guest")
    assert [r["name"] for r in guest["rules"]] == ["Voice"]
    assert guest["rules"][0]["ruleLogicalId"] == copied["rules"][0]["ruleLogicalId"]
    assert guest["defaults"][0]["action"] == copied["defaults"][0]["action"]


async def test_an_add_of_defaults_given_as_changes_takes_the_assignments_ids() -> None:
    state = await global_only()
    copied = copied_guest_entry()
    copied["defaults"] = {d["name"]: d for d in copied["defaults"]}

    _ = await patch(state, QOS, {"Guest": add(copied)})

    assert stored_default_ids(state) == [
        d["ruleLogicalId"] for d in assigned_defaults(state)
    ]


async def test_a_list_item_that_copies_an_entry_takes_the_assignments_defaults_ids() -> (  # noqa: E501 (a test's name)
    None
):
    state = await global_only()
    first = kept(state, QOS)
    copied = copied_guest_entry()

    _ = await patch(state, QOS, [first, copied])

    ids = [d["ruleLogicalId"] for d in assigned_defaults(state)]
    assert stored_default_ids(state) == ids
    assert not set(ids) & {d["ruleLogicalId"] for d in copied["defaults"]}


@pytest.mark.parametrize("form", ["add", "list"])
async def test_a_copied_entry_whose_default_differs_is_still_refused(form: str) -> None:
    state = await global_only()
    copied = copied_guest_entry()
    copied["defaults"][1]["match"] = {"appid": 9}
    before = dumps(state)

    with pytest.raises(PatchError) as raised:
        _ = await patch(
            state,
            QOS,
            {"Guest": add(copied)} if form == "add" else [kept(state, QOS), copied],
        )

    assert raised.value.kind == "fixed_list"
    assert "match" in raised.value.message
    assert dumps(state) == before


async def test_a_copied_entry_with_its_defaults_reordered_is_still_refused() -> None:
    state = await global_only()
    copied = copied_guest_entry()
    copied["defaults"].reverse()

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"Guest": add(copied)})

    assert raised.value.kind == "fixed_list"


# --- an orphan can go, nothing else ---------------------------------------------------


async def orphan_in(module: str) -> ConfigState:
    """A profile whose device settings lack Guest.

    *module* (QoS or firewall) has Guest.
    """
    return await profile_state(
        [ds_entry(GLOBAL)],
        [qos_entry(GLOBAL)] + ([qos_entry(GUEST)] if module == QOS else []),
        [firewall_entry(GLOBAL)]
        + ([firewall_entry(GUEST)] if module == FIREWALL else []),
    )


@pytest.mark.parametrize("module", [QOS, FIREWALL])
async def test_deleting_an_orphan_is_accepted(module: str) -> None:
    state = await orphan_in(module)

    _ = await patch(state, module, {"Guest": delete()})

    plan = state.plan()
    assert [m.name for m in plan.changed] == [module]
    assert names_in(state, module) == ["Global Segment"]


@pytest.mark.parametrize("module", [QOS, FIREWALL])
async def test_a_list_value_without_an_orphan_is_accepted(module: str) -> None:
    state = await orphan_in(module)
    first = kept(state, module)

    _ = await patch(state, module, [first])

    assert [m.name for m in state.plan().changed] == [module]
    assert names_in(state, module) == ["Global Segment"]


@pytest.mark.parametrize("module", ["qos", "firewall", "device_settings"])
@pytest.mark.parametrize(
    "form",
    [
        pytest.param("delete", id="delete"),
        pytest.param("list-without-it", id="a list value that leaves one out"),
        pytest.param("empty-list", id="an empty list"),
    ],
)
async def test_every_other_removal_raises(module: str, form: str) -> None:
    state = await guest_everywhere()
    before = dumps(state)
    rendered = state.plan().render()
    wire = {"qos": QOS, "firewall": FIREWALL, "device_settings": DS}[module]
    first = kept(state, wire)
    segments: object = {
        "delete": {"Guest": delete()},
        "list-without-it": [first],
        "empty-list": [],
    }[form]

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, module, segments)

    assert raised.value.kind == "segment_rule"
    assert raised.value.path in (Path(("segments",)), GUEST_AT)
    assert "removes" in raised.value.message
    assert "all three" in raised.value.message
    assert "orphan" in raised.value.message
    assert dumps(state) == before
    assert state.plan().render() == rendered


async def test_removing_a_segment_device_settings_has_raises_even_from_qos_without_it() -> (  # noqa: E501 (a test's name)
    None
):
    # Guest is in device settings and firewall; QoS lacks it. Removing it from
    # firewall is a removal, not an orphan.
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )

    with pytest.raises(PatchError, match="removes Guest") as raised:
        _ = await patch(state, FIREWALL, {"Guest": delete()})

    assert raised.value.kind == "segment_rule"


# --- a descriptor is not rewritten ----------------------------------------------------


@pytest.mark.parametrize(
    ("segments", "detail"),
    [
        pytest.param(
            {"Guest": {"segment": {"segment_id": 4, "name": "Voice"}}},
            "removes Guest",
            id="rename-and-re-ID",
        ),
        pytest.param(
            {"Guest": {"segment": {"segment_id": 7}}},
            "re-describes Guest",
            id="segment-id",
        ),
        pytest.param(
            {"Guest": {"segment": {"type": "CDE"}}},
            "re-describes Guest",
            id="type",
        ),
        pytest.param(
            {
                "Guest": {
                    "segment": {
                        "segment_logical_id": "00000000-0000-4000-8000-000000000001"
                    }
                }
            },
            "re-describes Guest",
            id="logical-id",
        ),
        pytest.param(
            {"Guest": {"segment": {"name": "Visitors"}}},
            "re-describes Guest",
            id="name",
        ),
    ],
)
@pytest.mark.parametrize("module", ["qos", "firewall", "device_settings"])
async def test_a_descriptor_rewrite_raises(
    module: str, segments: dict[str, object], detail: str
) -> None:
    state = await guest_everywhere()
    before = dumps(state)
    rendered = state.plan().render()

    with pytest.raises(
        PatchError, match="a patch cannot change which segments a profile has"
    ) as raised:
        _ = await patch(state, module, segments)

    assert raised.value.kind == "segment_rule"
    assert raised.value.path == Path(("segments",))
    assert detail in raised.value.message
    assert dumps(state) == before
    assert state.plan().render() == rendered


@pytest.mark.parametrize("module", ["qos", "firewall", "device_settings"])
async def test_a_descriptor_rewritten_into_anothers_id_raises(module: str) -> None:
    state = await guest_everywhere()
    before = dumps(state)

    with pytest.raises(PatchError):
        _ = await patch(state, module, {"Guest": {"segment": {"segment_id": 0}}})

    assert dumps(state) == before


async def test_a_descriptor_that_a_list_value_keeps_is_accepted() -> None:
    state = await guest_everywhere()
    reordered = [kept(state, QOS, 1), kept(state, QOS, 0)]

    _ = await patch(state, QOS, reordered)

    assert names_in(state, QOS) == ["Guest", "Global Segment"]


# --- all or nothing -------------------------------------------------------------------


async def test_a_failed_patch_leaves_all_three_modules_as_they_were() -> None:
    state = await global_only()
    before = dumps(state)
    rendered = state.plan().render()

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"Guest": add({"no_such_field": 1})})

    assert raised.value.kind == "unknown_field"
    assert dumps(state) == before
    assert state.plan().render() == rendered
    assert all(state.module(name).additions == () for name in MODULES)


async def test_a_failed_second_add_keeps_the_first() -> None:
    state = await global_only()
    _ = await patch(state, QOS, {"Guest": add({})})
    before = dumps(state)

    with pytest.raises(PatchError):
        _ = await patch(state, DS, {"Guest": add({"no_such_field": 1})})

    assert dumps(state) == before


# --- the saved assignment -------------------------------------------------------------


async def test_the_saved_assignment_reads_back_and_a_second_add_raises_exists() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)
    _ = await pipeline.apply(
        state,
        module="qos",
        patch={"segments": {"Guest": add({"rules": [rule("Box")]})}},
    )

    result = await pipeline.save(state)

    assert [str(m) for m in result.modules_updated] == [DS, QOS, FIREWALL]
    assert state.plan().changed == ()
    assert state.plan().render() == state.plan().render()
    for module in ("qos", "firewall", "device_settings"):
        with pytest.raises(PatchError) as raised:
            _ = await pipeline.apply(
                state, module=module, patch={"segments": {"Guest": add({})}}
            )
        assert raised.value.kind == "exists"


# --- the other segments ---------------------------------------------------------------


async def test_two_adds_in_one_patch_assign_two_segments() -> None:
    state = await global_only()

    _ = await patch(state, QOS, {"Guest": add({}), "Voice": add({})})

    assert names_in(state, FIREWALL) == ["Global Segment", "Guest", "Voice"]
    assert [m.added_segments for m in state.plan().changed] == [("Guest", "Voice")] * 3


async def test_two_selectors_for_one_segment_raise_duplicate() -> None:
    state = await global_only()
    before = dumps(state)

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"Guest": add({}), "guest": add({})})

    assert raised.value.kind == "duplicate"
    assert dumps(state) == before


async def test_a_change_to_a_segment_the_module_lacks_is_no_item() -> None:
    state = await global_only()

    with pytest.raises(PatchError) as raised:
        _ = await patch(state, QOS, {"Voice": {"rules": []}})

    assert raised.value.kind == "no_item"


async def test_a_second_add_to_the_same_module_before_a_save_is_merged_again() -> None:
    state = await global_only()

    _ = await patch(state, QOS, {"Guest": add({"rules": [rule("Box")]})})
    _ = await patch(state, QOS, {"Guest": add({"rules": [rule("Video")]})})

    guest = entry_of(plan_of(state, QOS), "Guest")
    assert [r["name"] for r in guest["rules"]] == ["Video"]
    assert [m.added_segments for m in state.plan().changed] == [("Guest",)] * 3
