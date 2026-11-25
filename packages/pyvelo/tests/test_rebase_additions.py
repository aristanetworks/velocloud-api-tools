"""``rebase_onto`` of a profile module that carries additions and repaired defaults.

The caller's edits go onto the fresh data plus the same additions, and the
repaired entries go back as planned. A fresh module that already holds an added
segment is a conflict, and nothing is sent.
"""

from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import ConfigConflictError
from pyvelo.client.models.configuration.profile_segments import new_segment_entry
from pyvelo.client.module_write import ModulePlan, plan_module, rebase_onto
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import Profile
from pyvelo.testing import make_module_json
from refs_helpers import CONFIG, QOS_ID
from segment_helpers import GLOBAL, GUEST, VOICE, identity, qos_entry

RATE: dict[str, Any] = {"enabled": False, "inputType": "percent", "value": 1}


def qos_at(
    version: str, *entries: dict[str, Any], enabled: bool = False
) -> QosConfigurationModule[Profile]:
    """A profile QoS module at *version* with *entries* and a rate limit.

    The rate limit is *enabled* or not.
    """
    data = {
        "segments": list(entries),
        "serviceRateLimit": {**RATE, "enabled": enabled},
    }
    row = make_module_json(
        "QOS", data, module_id=QOS_ID, configuration_id=CONFIG, version=version
    )
    return QosConfigurationModule[Profile].model_validate(row)


def guest_added(
    module: QosConfigurationModule[Profile], *, edited: bool = True
) -> ModulePlan:
    """The plan of *module* with Guest added.

    Its first rule is renamed to ``Box2`` when *edited*.
    """
    item: Any = module.add_segment_entry(new_segment_entry("QOS", identity(GUEST)))
    if edited:
        item.rules[0].name = "Box2"
    return plan_module(module, via_pipeline=True)


def test_rebase_onto_carries_the_additions() -> None:
    qos = qos_at("q1", qos_entry(GLOBAL))
    plan = guest_added(qos)
    assert [str(c.path) for c in plan.changes] == ["segments[Guest].rules[Box].name"]
    fresh = qos_at("q2", qos_entry(GLOBAL), enabled=True)

    rebased, rebased_plan, merged = rebase_onto(qos, plan, fresh)

    assert rebased.version == "q2"
    assert rebased.additions == qos.additions
    assert fresh.additions == ()
    assert rebased_plan.operation == "update"
    assert rebased_plan.added_segments == ("Guest",)
    assert rebased_plan.changes == plan.changes
    # Guest's entry as planned, and the other writer's change beside it.
    assert rebased_plan.data["segments"] == plan.data["segments"]
    assert rebased_plan.data["serviceRateLimit"]["enabled"] is True
    assert [(str(c.path), c.as_read, c.stored, c.merged) for c in merged] == [
        ("serviceRateLimit.enabled", False, True, True)
    ]


def test_rebase_onto_carries_the_repaired_entries() -> None:
    qos = qos_at("q1", qos_entry(GLOBAL, defaults=MISSING))
    plan = guest_added(qos, edited=False)
    (missing,) = [m for m in plan.missing_defaults if m.repaired]
    assert len(missing.names) == 4
    fresh = qos_at("q2", qos_entry(GLOBAL, defaults=MISSING), enabled=True)

    _, rebased_plan, _ = rebase_onto(qos, plan, fresh)

    assert rebased_plan.data["segments"] == plan.data["segments"]  # the same IDs
    assert rebased_plan.missing_defaults == plan.missing_defaults
    assert rebased_plan.added_segments == ("Guest",)
    assert rebased_plan.changes == plan.changes == ()


def test_a_fresh_module_that_holds_an_added_segment_is_a_conflict() -> None:
    qos = qos_at("q1", qos_entry(GLOBAL))
    plan = guest_added(qos)
    fresh = qos_at("q2", qos_entry(GLOBAL), qos_entry(GUEST))

    with pytest.raises(ConfigConflictError) as caught:
        _ = rebase_onto(qos, plan, fresh)

    error = caught.value
    assert (error.stated, error.sent) == (False, False)
    assert error.module is fresh
    assert error.detail == (
        "the module changed on the VCO after this save wrote device settings: it "
        "now holds segment 'Guest', which this save adds; nothing was sent"
    )
    (clash,) = error.lost_edits
    assert str(clash.path) == "segments[Guest]"
    assert clash.as_read is MISSING
    assert clash.stored == fresh.raw_data["segments"][1]  # pyright: ignore[reportIndexIssue]
    assert clash.clash


def test_a_fresh_module_that_holds_several_added_segments_reports_each() -> None:
    qos = qos_at("q1", qos_entry(GLOBAL))
    for segment in (GUEST, VOICE):
        _ = qos.add_segment_entry(new_segment_entry("QOS", identity(segment)))
    plan = plan_module(qos, via_pipeline=True)
    fresh = qos_at("q2", qos_entry(GLOBAL), qos_entry(GUEST), qos_entry(VOICE))

    with pytest.raises(ConfigConflictError) as caught:
        _ = rebase_onto(qos, plan, fresh)

    error = caught.value
    assert "segments 'Guest', 'Voice'" in error.detail
    assert [str(c.path) for c in error.lost_edits] == [
        "segments[Guest]",
        "segments[Voice]",
    ]
    assert (error.stated, error.sent) == (False, False)


def test_a_fresh_module_that_lacks_the_segment_added_defaults_go_into_is_a_conflict() -> (  # noqa: E501 (a test's name)
    None
):
    qos = qos_at("q1", qos_entry(GLOBAL, defaults=MISSING))
    rules = new_segment_entry("QOS", identity(GLOBAL))["defaults"]
    qos.add_default_entries(GLOBAL[0], rules)
    plan = plan_module(qos)
    assert [type(a).__name__ for a in qos.additions] == ["AddedDefaults"]
    fresh = qos_at("q2", qos_entry(GUEST))

    with pytest.raises(ConfigConflictError) as caught:
        _ = rebase_onto(qos, plan, fresh)

    error = caught.value
    assert (error.stated, error.sent) == (False, False)
    assert error.module is fresh
    assert error.detail == (
        "the module changed on the VCO after this save wrote device settings: it "
        "no longer holds segment 'Global Segment', which this save adds defaults "
        "to; nothing was sent"
    )


def test_a_fresh_module_that_holds_the_segment_added_defaults_go_into_is_rebased() -> (
    None
):
    qos = qos_at("q1", qos_entry(GLOBAL, defaults=MISSING))
    rules = new_segment_entry("QOS", identity(GLOBAL))["defaults"]
    qos.add_default_entries(GLOBAL[0], rules)
    plan = plan_module(qos)
    fresh = qos_at("q2", qos_entry(GLOBAL, defaults=MISSING), enabled=True)

    rebased, rebased_plan, _ = rebase_onto(qos, plan, fresh)

    assert rebased.additions == qos.additions
    assert rebased_plan.data["segments"] == plan.data["segments"]
