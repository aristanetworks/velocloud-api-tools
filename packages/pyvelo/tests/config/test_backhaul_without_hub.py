"""``ConfigState.plan`` refuses a QoS rule that backhauls to hubs and names none.

A route action whose ``routePolicy`` is ``"backhaul"`` and whose ``routeCfg.type``
is ``"edge"`` sends its traffic to the hubs of its ``backhaulEdges``. One left with
none is refused at ``plan()`` (``BackhaulWithoutHubError``), and ``apply`` accepts
the edit. One that was so as read is a ``backhaul_without_hub_kept`` warning.
"""

import json
from typing import Any

import pytest
from hub_helpers import HUB_A, HUB_B, Hub, backhaul_entry, ds_with_vpn, hub_row
from hub_state import (
    GUEST_SERVICES,
    guest_backhaul_state,
    hub_backhaul_rule,
    hub_edge_state,
    hub_pipeline,
    profile_hub_rows,
)
from pyvelo.config import (
    BackhaulWithoutHubError,
    ConfigPipeline,
    ConfigState,
    DanglingReferenceError,
)
from pyvelo.core import add, delete, move
from refs_helpers import segment_row
from segment_helpers import GLOBAL, ds_entry, qos_entry

DS = "deviceSettings"
QOS = "QOS"
UPDATE = "configuration/updateConfigurationModule"
CLOUD = "edge2CloudRouteAction"
WEB_PATH = "segments[Guest].rules[Web].action.edge_to_cloud_route_action"


async def edit(state: ConfigState, module: str, patch: dict[str, Any]) -> None:
    _ = await ConfigPipeline(client=None).apply(state, module=module, patch=patch)


def to_rule(rule: str, action: dict[str, Any], route_action: str = CLOUD) -> Any:
    """The QoS patch that sends *action* to *route_action* of Guest's rule *rule*."""
    return {
        "segments": {"Guest": {"rules": {rule: {"action": {route_action: action}}}}}
    }


def out_of_rule(rule: str, hub: Hub, route_action: str = CLOUD) -> Any:
    return to_rule(
        rule, {"routeCfg": {"backhaulEdges": {hub.name: delete()}}}, route_action
    )


def refused(state: ConfigState) -> BackhaulWithoutHubError:
    with pytest.raises(BackhaulWithoutHubError) as error:
        _ = state.plan()
    return error.value


WAYS = ("give the rule a hub", "route it another way", "delete the rule")


def shapes(
    error: BackhaulWithoutHubError, ways: tuple[str, ...] = WAYS
) -> dict[str, Any]:
    """The patches the message shows for *ways*, by what each does, as JSON."""
    text = str(error)
    found: dict[str, Any] = {}
    for way in ways:
        start = text.index("{", text.index(way))
        found[way], _ = json.JSONDecoder().raw_decode(text, start)
    return found


async def test_deleting_the_only_hub_is_accepted_by_apply_and_refused_by_plan() -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web", HUB_A))
    assert state.plan().warnings == ()
    await edit(state, QOS, out_of_rule("Web", HUB_A))  # accepted: plan() is the judge

    error = refused(state)

    (place,) = error.places
    assert (place.module, str(place.path), place.segment_name, place.rule_name) == (
        QOS,
        WEB_PATH,
        "Guest",
        "Web",
    )
    text = str(error)
    assert WEB_PATH in text
    assert "sends its traffic by backhaul and names no hub" in text
    assert "a rule that backhauls nowhere" in text
    assert "to QOS" in text
    assert "`revert`" in text
    assert shapes(error) == {
        "give the rule a hub": to_rule(
            "Web",
            {"routeCfg": {"backhaulEdges": {"+": add({"name": "<hub name>"})}}},
        ),
        "route it another way": to_rule(
            "Web", {"routePolicy": "<auto, direct or gateway>"}
        ),
        "delete the rule": {"segments": {"Guest": {"rules": {"Web": delete()}}}},
    }


async def test_a_save_and_a_dry_run_are_refused_and_send_nothing() -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web", HUB_A))
    await edit(state, QOS, out_of_rule("Web", HUB_A))
    pipeline, store = hub_pipeline(state)

    with pytest.raises(BackhaulWithoutHubError):
        _ = await pipeline.save(state, dry_run=True)
    with pytest.raises(BackhaulWithoutHubError):
        _ = await pipeline.save(state)

    assert [m for m, _ in store.requests if m == UPDATE] == []


@pytest.mark.parametrize(
    ("way", "fill"),
    [
        ("give the rule a hub", HUB_B.name),
        ("route it another way", "gateway"),
        ("delete the rule", None),
    ],
)
async def test_each_patch_the_message_shows_mends_the_rule(
    way: str, fill: str | None
) -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web", HUB_A))
    await edit(state, QOS, out_of_rule("Web", HUB_A))
    shown = json.dumps(shapes(refused(state))[way])
    if fill is not None:
        shown = shown.replace("<hub name>", fill).replace(
            "<auto, direct or gateway>", fill
        )

    await edit(state, QOS, json.loads(shown))

    assert [w.code for w in state.plan().warnings] == []


async def test_deleting_one_hub_of_two_is_accepted() -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web", HUB_A, HUB_B))
    await edit(state, QOS, out_of_rule("Web", HUB_A))

    assert state.plan().warnings == ()


async def test_a_new_rule_that_backhauls_to_no_hub_is_refused() -> None:
    state = await guest_backhaul_state()
    rule = hub_backhaul_rule("Web")
    await edit(state, QOS, {"segments": {"Guest": {"rules": {"+": add(rule)}}}})

    (place,) = refused(state).places

    assert str(place.path) == WEB_PATH


async def test_a_rule_set_to_backhaul_with_no_hub_is_refused() -> None:
    rule = hub_backhaul_rule("Web")
    rule["action"][CLOUD]["routePolicy"] = "gateway"
    state = await guest_backhaul_state(rule)
    assert state.plan().warnings == ()

    await edit(state, QOS, to_rule("Web", {"routePolicy": "backhaul"}))

    (place,) = refused(state).places
    assert str(place.path) == WEB_PATH


@pytest.mark.parametrize(
    ("route_action", "field"),
    [
        ("edge2EdgeRouteAction", "edge_to_edge_route_action"),
        ("edge2DataCenterRouteAction", "edge_to_data_center_route_action"),
    ],
)
async def test_each_route_action_of_a_rule_is_checked(
    route_action: str, field: str
) -> None:
    state = await guest_backhaul_state(
        hub_backhaul_rule("Web", HUB_A, route_action=route_action)
    )
    await edit(state, QOS, out_of_rule("Web", HUB_A, route_action))

    (place,) = refused(state).places

    assert str(place.path) == f"segments[Guest].rules[Web].action.{field}"


async def test_a_rule_with_no_hub_as_read_is_kept_with_a_warning() -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web"))

    (warning,) = state.plan().warnings

    assert warning.code == "backhaul_without_hub_kept"
    assert warning.path == f"{QOS}: {WEB_PATH}"
    assert "it was so as read" in warning.message


async def test_an_edit_elsewhere_keeps_the_rule_as_read_with_a_warning() -> None:
    state = await guest_backhaul_state(
        hub_backhaul_rule("Web"), hub_backhaul_rule("Mail", HUB_A)
    )
    await edit(state, QOS, to_rule("Mail", {"routePolicy": "gateway"}))

    plan = state.plan()

    assert [m.name for m in plan.changed] == [QOS]
    assert [(w.code, w.path) for w in plan.warnings] == [
        ("backhaul_without_hub_kept", f"{QOS}: {WEB_PATH}")
    ]


async def test_giving_the_rule_as_read_a_hub_ends_the_warning() -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web"))
    entry = backhaul_entry(HUB_A, GUEST_SERVICES[HUB_A][1])

    await edit(
        state, QOS, to_rule("Web", {"routeCfg": {"backhaulEdges": {"+": add(entry)}}})
    )

    assert state.plan().warnings == ()


async def test_an_edge_rule_that_backhauls_to_no_hub_is_refused() -> None:
    inheriting = qos_entry(GLOBAL)
    del inheriting["cosMapping"]  # the template's is stored with its flag off
    state = await hub_edge_state(
        [ds_entry(GLOBAL)],
        [inheriting],
        profile_ds=[ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,))],
        profile_qos=[qos_entry(GLOBAL)],
        profile_refs=profile_hub_rows(
            segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
            hub_row(
                HUB_A,
                service_id=GUEST_SERVICES[HUB_A][0],
                service_logical_id=GUEST_SERVICES[HUB_A][1],
                segment=GLOBAL,
                roles=("hub", "backhaul"),
            ),
        ),
    )
    rule = hub_backhaul_rule("Web")
    await edit(
        state, QOS, {"segments": {"Global Segment": {"rules": {"+": add(rule)}}}}
    )

    error = refused(state)

    (place,) = error.places
    assert (place.module, place.segment_name, place.rule_name) == (
        QOS,
        "Global Segment",
        "Web",
    )
    assert '{"segments": {"Global Segment": {"rules": {"Web"' in str(error)


async def test_two_rules_left_with_no_hub_are_both_listed() -> None:
    state = await guest_backhaul_state(
        hub_backhaul_rule("Web", HUB_A), hub_backhaul_rule("Mail", HUB_A)
    )
    await edit(state, QOS, out_of_rule("Web", HUB_A))
    await edit(state, QOS, out_of_rule("Mail", HUB_A))

    error = refused(state)

    assert [p.rule_name for p in error.places] == ["Web", "Mail"]
    text = str(error)
    assert "rules[Web]" in text
    assert "rules[Mail]" in text
    assert "mended the same way" in text
    assert '"Mail": {"_op": "delete"}' not in text  # the shapes are the first's


# -- a default rule ------------------------------------------------------------------

DEFAULT = "Default-Any-UDP"
NO_HUB = {"routePolicy": "backhaul", "routeCfg": {"type": "edge"}}


def to_default(action: dict[str, Any], segment: str = "Guest") -> Any:
    """The QoS patch that sends *action* to the Edge to cloud route of ``DEFAULT``."""
    rule = {"action": {CLOUD: action}}
    return {"segments": {segment: {"defaults": {DEFAULT: rule}}}}


def fill(shape: Any, value: str) -> Any:
    shown = json.dumps(shape)
    shown = shown.replace("<hub name>", value)
    return json.loads(shown.replace("<auto, direct or gateway>", value))


async def test_a_default_rule_left_with_no_hub_is_refused_with_no_delete() -> None:
    state = await guest_backhaul_state()
    await edit(state, QOS, to_default(NO_HUB))

    error = refused(state)

    (place,) = error.places
    assert str(place.path).startswith("segments[Guest].defaults[Default-Any-UDP]")
    assert place.rule_name == DEFAULT
    text = str(error)
    assert "delete the rule" not in text
    assert '"_op": "delete"' not in text
    assert "one of the segment's default rules, which cannot be deleted" in text


@pytest.mark.parametrize(
    ("way", "value"),
    [("give the rule a hub", HUB_B.name), ("route it another way", "gateway")],
)
async def test_each_way_shown_for_a_default_rule_mends_it(way: str, value: str) -> None:
    state = await guest_backhaul_state()
    await edit(state, QOS, to_default(NO_HUB))
    shown = shapes(refused(state), WAYS[:2])[way]

    await edit(state, QOS, fill(shown, value))

    assert [w.code for w in state.plan().warnings] == []


async def test_the_ways_shown_are_those_of_the_first_place() -> None:
    state = await guest_backhaul_state()
    await edit(state, QOS, to_default(NO_HUB, segment="Global Segment"))
    await edit(
        state,
        QOS,
        {"segments": {"Guest": {"rules": {"+": add(hub_backhaul_rule("Web"))}}}},
    )

    error = refused(state)

    assert [p.rule_name for p in error.places] == [DEFAULT, "Web"]
    text = str(error)
    assert '"Default-Any-UDP": {"_op": "delete"}' not in text
    assert "mended the same way, and one that is not a default rule can be" in text
    policy = shapes(error, WAYS[1:2])["route it another way"]
    await edit(state, QOS, fill(policy, "gateway"))
    (place,) = refused(state).places  # the default rule is mended
    assert place.rule_name == "Web"


@pytest.mark.parametrize(
    "mend",
    [
        {"routeCfg": {"backhaulEdges": {"+": add({"name": HUB_B.name})}}},
        {"routePolicy": "gateway"},
    ],
    ids=["a hub", "another way"],
)
async def test_a_default_rule_listed_after_a_rule_is_named_as_not_deletable(
    mend: dict[str, Any],
) -> None:
    state = await guest_backhaul_state()
    await edit(state, QOS, to_default(NO_HUB))
    await edit(
        state,
        QOS,
        {"segments": {"Guest": {"rules": {"+": add(hub_backhaul_rule("Web"))}}}},
    )

    error = refused(state)

    assert [p.rule_name for p in error.places] == ["Web", DEFAULT]
    assert (
        "The other rules are mended the same way, but these are default rules of "
        "their segment, which cannot be deleted: rule 'Default-Any-UDP' of segment "
        "'Guest'; give each a hub or route it another way."
    ) in str(error)
    await edit(state, QOS, to_default(mend))
    (place,) = refused(state).places  # the default rule is mended
    assert place.rule_name == "Web"


# -- guards: what the check already does ---------------------------------------------


async def test_a_rule_with_no_hub_as_read_moved_in_its_list_is_kept() -> None:
    state = await guest_backhaul_state(
        hub_backhaul_rule("Web"), hub_backhaul_rule("Mail", HUB_A)
    )
    await edit(state, QOS, {"segments": {"Guest": {"rules": {"Web": move(last=True)}}}})

    plan = state.plan()

    assert [m.name for m in plan.changed] == [QOS]
    assert [w.code for w in plan.warnings] == ["backhaul_without_hub_kept"]


async def test_emptying_the_hubs_by_assignment_is_refused() -> None:
    state = await guest_backhaul_state(hub_backhaul_rule("Web", HUB_A))
    rule = state.profile.qos.segments[1].rules[0]
    rule.action.edge_to_cloud_route_action.route_cfg.backhaul_edges = []

    (place,) = refused(state).places

    assert str(place.path) == WEB_PATH


async def test_setting_backhaul_by_assignment_on_a_rule_with_no_hub_is_refused() -> (
    None
):
    rule = hub_backhaul_rule("Web")
    rule["action"][CLOUD]["routePolicy"] = "gateway"
    state = await guest_backhaul_state(rule)
    held = state.profile.qos.segments[1].rules[0]
    held.action.edge_to_cloud_route_action.route_policy = "backhaul"

    (place,) = refused(state).places

    assert str(place.path) == WEB_PATH


async def test_backhaul_edges_unset_as_read_warns() -> None:
    rule = hub_backhaul_rule("Web")
    del rule["action"][CLOUD]["routeCfg"]["backhaulEdges"]
    state = await guest_backhaul_state(rule)

    (warning,) = state.plan().warnings

    assert (warning.code, warning.path) == (
        "backhaul_without_hub_kept",
        f"{QOS}: {WEB_PATH}",
    )


async def test_a_rule_with_no_hub_in_an_assigned_segment_is_refused() -> None:
    state = await guest_backhaul_state()
    pipeline, _ = hub_pipeline(state)
    _ = await pipeline.assign_segment(state, "Voice")
    rule = hub_backhaul_rule("Calls")  # the segment's template rules have a Web
    await edit(state, QOS, {"segments": {"Voice": {"rules": {"+": add(rule)}}}})

    (place,) = refused(state).places

    assert (place.segment_name, place.rule_name) == ("Voice", "Calls")


async def test_on_an_edge_no_patch_takes_the_only_hub_out_of_a_rule() -> None:
    inheriting = qos_entry(GLOBAL)
    del inheriting["cosMapping"]  # the template's is stored with its flag off
    state = await hub_edge_state(
        [ds_entry(GLOBAL)],
        [inheriting],
        profile_ds=[ds_with_vpn(GLOBAL, hubs=(HUB_A, HUB_B), backhaul=(HUB_A,))],
        profile_qos=[qos_entry(GLOBAL)],
        profile_refs=profile_hub_rows(
            segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
            *(
                hub_row(
                    hub,
                    service_id=service_id,
                    service_logical_id=service,
                    segment=GLOBAL,
                    roles=("hub", "backhaul") if hub == HUB_A else ("hub",),
                )
                for hub, (service_id, service) in GUEST_SERVICES.items()
            ),
        ),
    )
    rule = hub_backhaul_rule("Web", HUB_B)  # Hub-B is no backhaul hub
    await edit(
        state, QOS, {"segments": {"Global Segment": {"rules": {"+": add(rule)}}}}
    )

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    assert [m.kind for m in error.value.mending] == ["add_member"]
    assert error.value.only_hubs == error.value.references
    assert error.value.on_edge


async def test_a_default_rule_with_two_hubless_route_actions_is_named_once() -> None:
    state = await guest_backhaul_state()
    await edit(state, QOS, to_default(NO_HUB))
    data_center = {"action": {"edge2DataCenterRouteAction": NO_HUB}}
    await edit(
        state, QOS, {"segments": {"Guest": {"defaults": {DEFAULT: data_center}}}}
    )
    await edit(
        state,
        QOS,
        {"segments": {"Guest": {"rules": {"+": add(hub_backhaul_rule("Web"))}}}},
    )

    error = refused(state)

    assert [p.rule_name for p in error.places] == ["Web", DEFAULT, DEFAULT]
    assert (
        "which cannot be deleted: rule 'Default-Any-UDP' of segment 'Guest'; give"
    ) in str(error)
