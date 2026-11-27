"""``ConfigPipeline.assign_segment`` adds an enterprise segment to a profile, in memory.

One call adds the UI's factory entry for the segment to each of the profile's three
modules.

Each state is built by ``profile_state`` (a profile target, a segment resolver
loaded with Global, Guest and Voice); the writes go to a ``ModuleStore``.
"""

import copy
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

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
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    QosConfigurationModule,
)
from pyvelo.client.schemas.enterprise import NetworkSegment
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.profile_segments import (
    apply_assignment,
    assign_on_trial,
    plan_assignment,
    segment_modules,
)
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.state import ConfigState
from pyvelo.core.identity import ResolutionError
from pyvelo.testing import network_segment_wire
from refs_helpers import QOS_ID, SEG1_OBJECT
from segment_helpers import (
    GLOBAL,
    GUEST,
    ds_entry,
    firewall_entry,
    identity,
    qos_entry,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)

if TYPE_CHECKING:
    from pyvelo.core.layers import Profile

DS = "deviceSettings"
QOS = "QOS"
FIREWALL = "firewall"


async def global_only() -> ConfigState:
    return await profile_state(
        [ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )


def plan_of(state: ConfigState, module: str) -> ModulePlan:
    (planned,) = [m for m in state.plan().modules if m.name == module]
    return planned


def entry_of(planned: ModulePlan, name: str) -> dict[str, Any]:
    (entry,) = [s for s in planned.data["segments"] if s["segment"]["name"] == name]
    return entry


def without_rule_ids(entry: dict[str, Any]) -> dict[str, Any]:
    """*entry* with the ``ruleLogicalId`` of each rule and default taken out."""
    entry = copy.deepcopy(entry)
    for key in ("rules", "defaults"):
        for rule in entry.get(key) or []:
            del rule["ruleLogicalId"]
    return entry


def rule(name: str, rule_id: str | None = None) -> dict[str, Any]:
    """A QoS rule as the wire has it, from the first template rule renamed."""
    made = default_rules()[0]
    made["name"] = name
    if rule_id is not None:
        made["ruleLogicalId"] = rule_id
    return made


# --- what an assignment makes ----------------------------------------------


async def test_assign_then_plan_writes_three_updates_device_settings_first() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)

    returned = await pipeline.assign_segment(state, "Guest")

    assert returned is state
    plan = state.plan()
    assert [(m.name, m.operation) for m in plan.modules] == [
        (DS, "update"),
        (QOS, "update"),
        (FIREWALL, "update"),
    ]
    assert all(m.added_segments == ("Guest",) for m in plan.modules)
    assert entry_of(plan.modules[0], "Guest") == new_segment_entry(DS, identity(GUEST))
    qos = entry_of(plan.modules[1], "Guest")
    assert without_rule_ids(qos) == without_rule_ids(
        new_segment_entry("QOS", identity(GUEST))
    )
    ids = [r["ruleLogicalId"] for r in (*qos["rules"], *qos["defaults"])]
    assert len(set(ids)) == len(ids)
    assert [uuid.UUID(i).version for i in ids] == [4] * len(ids)
    assert entry_of(plan.modules[2], "Guest") == new_segment_entry(
        FIREWALL, identity(GUEST)
    )
    (row,) = plan.modules[0].ref_changes
    assert (row.added, row.name) == (True, "deviceSettings:segment")
    assert row.row["enterpriseObjectId"] == SEG1_OBJECT


async def test_the_qos_entry_has_the_template_defaults_match_key_for_key() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)

    _ = await pipeline.assign_segment(state, "Guest")

    written = entry_of(plan_of(state, QOS), "Guest")["defaults"]
    template = default_rules()
    assert [d["name"] for d in written] == [t["name"] for t in template]
    assert [d["match"] for d in written] == [t["match"] for t in template]
    assert [d["action"] for d in written] == [t["action"] for t in template]
    assert state.plan().warnings == ()


async def test_a_segment_read_from_the_vco_is_assigned() -> None:
    state = await global_only()
    client = AsyncMock()
    client.get_enterprise_network_segments.return_value = [
        NetworkSegment.model_validate(
            network_segment_wire(
                "Global Segment", 0, object_id=1, logical_id=uuid.UUID(int=0)
            )
        ),
        NetworkSegment.model_validate(
            network_segment_wire(
                "Guest",
                2,
                object_id=SEG1_OBJECT,
                logical_id=uuid.UUID(GUEST[2]),
                type_="CDE",
            )
        ),
    ]
    resolver = SegmentResolver(client)
    await resolver.load(ResolverContext())
    state.resolvers.register(resolver)
    pipeline, _ = module_store_pipeline(state)

    await pipeline.assign_segment(state, "Guest")

    assert entry_of(plan_of(state, DS), "Guest")["segment"]["type"] == "CDE"
    for module in (DS, QOS, FIREWALL):
        entry_of(plan_of(state, module), "Guest")


async def test_assign_then_save_writes_the_three_modules() -> None:
    state = await global_only()
    pipeline, store = module_store_pipeline(state)

    _ = await pipeline.assign_segment(state, "Guest")
    result = await pipeline.save(state)

    assert [str(m) for m in result.modules_updated] == [DS, QOS, FIREWALL]
    assert state.plan().changed == ()
    names = [s["segment"]["name"] for s in store.rows[QOS_ID]["data"]["segments"]]
    assert names == ["Global Segment", "Guest"]


# --- qos_rules ---------------------------------------------------------------


async def test_qos_rules_take_the_place_of_the_template_rules() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)
    kept_id = str(uuid.uuid4())

    _ = await pipeline.assign_segment(
        state,
        "Guest",
        qos_rules=[
            rule("Box", kept_id),
            QosRule.model_validate(rule("Video")),
            {k: v for k, v in rule("Chat").items() if k != "ruleLogicalId"},
        ],
    )

    rules = entry_of(plan_of(state, QOS), "Guest")["rules"]
    assert [r["name"] for r in rules] == ["Box", "Video", "Chat"]
    assert rules[0]["ruleLogicalId"] == kept_id
    made = [r["ruleLogicalId"] for r in rules[1:]]
    assert len(set(made)) == 2
    assert kept_id not in made
    assert [uuid.UUID(i).version for i in made] == [4, 4]


async def test_qos_rules_empty_gives_no_rules() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)

    _ = await pipeline.assign_segment(state, "Guest", qos_rules=[])

    entry = entry_of(plan_of(state, QOS), "Guest")
    assert entry["rules"] == []
    assert len(entry["defaults"]) == 4


@pytest.mark.parametrize(
    "rules",
    [
        [rule("Box"), rule("Box")],
        [
            rule("Box", "11111111-1111-4111-8111-111111111111"),
            rule("Other", "11111111-1111-4111-8111-111111111111"),
        ],
    ],
    ids=["one name", "one ruleLogicalId"],
)
async def test_qos_rules_with_a_repeated_name_or_id_raise(
    rules: list[dict[str, Any]],
) -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)
    before = state.plan().render()

    with pytest.raises(ValueError, match="more than one rule with the"):
        _ = await pipeline.assign_segment(state, "Guest", qos_rules=rules)

    assert state.plan().render() == before


async def test_an_edit_before_the_save_is_a_change_from_the_template() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)
    _ = await pipeline.assign_segment(state, "Guest")

    _ = await pipeline.apply(
        state,
        module=QOS,
        patch={
            "segments": {
                "Guest": {
                    "rules": {"Box": {"action": {"edge_to_edge_encryption": False}}}
                }
            }
        },
    )

    qos = plan_of(state, QOS)
    assert len(qos.changes) == 1
    assert qos.added_segments == ("Guest",)


# --- what raises ---------------------------------------------------------------


async def test_an_edge_target_raises() -> None:
    state = make_state()
    pipeline = ConfigPipeline(None)

    with pytest.raises(ValueError, match="edge has the segments of its profile"):
        _ = await pipeline.assign_segment(state, "Guest")


async def test_a_profile_without_qos_raises() -> None:
    state = await global_only()
    qos = state.module(QOS)
    assert isinstance(qos, QosConfigurationModule)
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    modules.remove(cast("QosConfigurationModule[Profile]", qos))  # the profile layer
    pipeline, _ = module_store_pipeline(state)

    with pytest.raises(ValueError, match="business_policy"):
        _ = await pipeline.assign_segment(state, "Guest")


async def test_nothing_to_do_raises() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )
    pipeline, _ = module_store_pipeline(state)

    with pytest.raises(
        ValueError,
        match="Guest is in all three modules already; change it with a patch",
    ):
        _ = await pipeline.assign_segment(state, "Guest")

    assert state.plan().changed == ()


async def test_qos_rules_for_a_qos_entry_already_there_raise() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL)],
    )
    pipeline, _ = module_store_pipeline(state)

    with pytest.raises(ValueError, match="qos_rules"):
        _ = await pipeline.assign_segment(state, "Guest", qos_rules=[rule("Box")])

    assert state.plan().changed == ()


# --- the name --------------------------------------------------------------------


async def test_an_unknown_name_raises_resolution_error_with_suggestions() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)

    with pytest.raises(ResolutionError) as raised:
        _ = await pipeline.assign_segment(state, "Gest")

    assert "Guest" in str(raised.value)
    assert state.plan().changed == ()


async def test_a_name_in_another_case_assigns_the_enterprise_name() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)

    _ = await pipeline.assign_segment(state, "gUEST")

    assert plan_of(state, FIREWALL).added_segments == ("Guest",)


async def test_no_segment_resolver_raises_lookup_error_naming_load_resolvers() -> None:
    state = await global_only()
    state.resolvers = ResolverRegistry()
    pipeline, _ = module_store_pipeline(state)

    with pytest.raises(LookupError, match=r"pipeline\.load_resolvers\(state\)"):
        _ = await pipeline.assign_segment(state, "Guest")

    assert state.plan().changed == ()


# --- a profile that has part of it ----------------------------------------------------


async def test_a_profile_with_the_segment_in_one_module_is_completed() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL)],
        [firewall_entry(GLOBAL)],
    )
    pipeline, _ = module_store_pipeline(state)

    _ = await pipeline.assign_segment(state, "Guest")

    plan = state.plan()
    assert [(m.name, m.added_segments) for m in plan.modules] == [
        (DS, ()),
        (QOS, ("Guest",)),
        (FIREWALL, ("Guest",)),
    ]
    assert [m.name for m in plan.changed] == [QOS, FIREWALL]
    assert plan.segment_warnings == ()


async def test_an_existing_qos_entry_missing_defaults_gets_them_in_the_baseline() -> (
    None
):
    two = default_rules()[:2]
    for default in two:
        default["ruleLogicalId"] = str(uuid.uuid4())
    guest_ds = ds_entry(GUEST)
    guest_ds["vpn"] = {"conditionalBackhaul": True}
    state = await profile_state(
        [ds_entry(GLOBAL), guest_ds],
        [qos_entry(GLOBAL), qos_entry(GUEST, defaults=two)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )
    pipeline, _ = module_store_pipeline(state)

    _ = await pipeline.assign_segment(state, "Guest")

    plan = state.plan()
    assert [m.name for m in plan.changed] == [QOS]
    qos = plan.modules[1]
    defaults = {d["name"]: d for d in entry_of(qos, "Guest")["defaults"]}
    assert list(defaults) == [r["name"] for r in default_rules()]
    added = ("Default-Any-UDP", "Default-Any-Other")
    assert [defaults[n]["action"]["allowConditionalBh"] for n in added] == [True, True]
    assert "allowConditionalBh" not in defaults["Default-Internet-UDP"]["action"]
    assert all(
        d["ruleLogicalId"] not in {t["ruleLogicalId"] for t in two}
        for d in (defaults[n] for n in added)
    )
    assert [w.code for w in plan.warnings if w.code == "defaults_repaired"] == []


# --- all or nothing -------------------------------------------------------------------


async def test_a_failure_leaves_the_state_as_it_was() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)
    before = state.plan().render()

    with pytest.raises(ValueError, match="validation errors for QosRule"):
        _ = await pipeline.assign_segment(
            state, "Guest", qos_rules=[{"name": "Box", "match": "not a match"}]
        )

    assert state.plan().render() == before
    assert all(state.module(name).additions == () for name in (DS, QOS, FIREWALL))


async def test_assign_holds_the_lock() -> None:
    state = await global_only()
    pipeline, _ = module_store_pipeline(state)

    async with state.lock:
        with pytest.raises(RuntimeError, match="already holds the lock"):
            _ = await pipeline.assign_segment(state, "Guest")

    assert state.plan().changed == ()


# --- the shared helpers ---------------------------------------------------------------


async def test_a_module_not_at_the_profile_level_raises_naming_the_fix() -> None:
    state = await global_only()
    ds = state.module(DS)
    ds._config_context = None  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ValueError, match=r"load_profile.*for_profile") as exc:
        _ = segment_modules(state)

    assert str(exc.value).startswith(
        "The device_settings module is not at the profile level"
    )


async def test_a_profile_that_lacks_a_module_names_it_by_its_python_name() -> None:
    state = await global_only()
    modules = state.profile_config.modules
    assert isinstance(modules, list)
    modules[:] = [m for m in modules if m.name != QOS]

    with pytest.raises(ValueError, match="Inserting a module") as exc:
        _ = segment_modules(state)

    assert str(exc.value).startswith("The profile has no business_policy module")


async def test_the_trial_changes_nothing_and_raises_what_the_edit_raises() -> None:
    state = await global_only()
    modules = segment_modules(state)
    assignment = plan_assignment(state, identity(GUEST))
    seen: list[list[str]] = []

    def edit(trial: Mapping[str, AnyStoredModule]) -> None:
        seen.append([m.name for m in trial.values()])
        assert all(len(m.additions) == 1 for m in trial.values())
        raise KeyError("edit failed")

    with pytest.raises(KeyError, match="edit failed"):
        assign_on_trial(state, modules, [assignment], edit)

    assert seen == [[DS, QOS, FIREWALL]]
    assert all(m.additions == () for m in modules.values())
    assert state.plan().changed == ()

    assign_on_trial(state, modules, [assignment])
    apply_assignment(modules, assignment)
    assert all(len(m.additions) == 1 for m in modules.values())


async def test_an_assignment_made_once_has_one_set_of_ids() -> None:
    state = await global_only()
    modules = segment_modules(state)
    assignment = plan_assignment(state, identity(GUEST))

    apply_assignment(modules, assignment)

    (added,) = modules[QOS].additions
    assert isinstance(added, AddedSegment)
    assert added.entry == assignment.entries[QOS]
    assert added.entry is not assignment.entries[QOS]
