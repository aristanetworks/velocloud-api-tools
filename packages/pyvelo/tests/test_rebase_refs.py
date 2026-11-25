"""``rebase_onto`` of a QoS plan that adds or removes a backhaul hub row.

The rebased module holds the fresh module's data and refs, so its plan derives
the caller's edit against the refs the VCO holds now: the fresh refs, with the
one row the edit adds or without the one it removes.
"""

from typing import Any

from hub_helpers import HUB_A, HUB_B, backhaul_entry
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.module_write import ModulePlan, plan_module, rebase_onto
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import ConfigContext, Profile
from pyvelo.testing import make_module_json
from refs_helpers import CONFIG, QOS_ID
from segment_helpers import GLOBAL, qos_entry

from .test_backhaul_refs import (
    OTHER_REF,
    OTHER_ROW,
    SERVICE_A,
    SERVICE_A_ID,
    SERVICE_B,
    SERVICE_B_ID,
    edited,
    new_row,
    profile_services,
    qos_row,
    rows_of,
    rule,
)

RATE: dict[str, Any] = {"enabled": False, "inputType": "percent", "value": 1}
OTHER_WRITERS_ROW = {**OTHER_ROW, "enterpriseObjectId": 10}


def qos_at(
    version: str,
    rules: list[dict[str, Any]],
    refs: dict[str, Any],
    *,
    enabled: bool = False,
) -> QosConfigurationModule[Profile]:
    """A profile QoS module at *version* with *rules* in the Global Segment and *refs*.

    Its rate limit is *enabled* or not.
    """
    data = {
        "segments": [qos_entry(GLOBAL, rules=rules)],
        "serviceRateLimit": {**RATE, "enabled": enabled},
    }
    row = make_module_json(
        "QOS",
        data,
        module_id=QOS_ID,
        configuration_id=CONFIG,
        version=version,
        refs=refs,
    )
    return QosConfigurationModule[Profile].model_validate(
        row, context={"refs_loaded": True}
    )


def planned(
    module: QosConfigurationModule[Profile], data: QosData[Profile]
) -> ModulePlan:
    return plan_module(
        module,
        data,
        level=ConfigContext.PROFILE,
        profile_device_settings=profile_services(),
    )


def rebased(
    module: QosConfigurationModule[Profile],
    plan: ModulePlan,
    fresh: QosConfigurationModule[Profile],
) -> tuple[QosConfigurationModule[Profile], ModulePlan]:
    """*plan* rebased onto *fresh*.

    With the profile's device settings as the plan was made with.
    """
    onto, onto_plan, _ = rebase_onto(
        module, plan, fresh, profile_device_settings=profile_services()
    )
    return onto, onto_plan


def test_a_rebased_plan_adds_its_row_to_the_fresh_refs() -> None:
    row_b = qos_row(HUB_B, SERVICE_B_ID, SERVICE_B)
    module = qos_at(
        "q1",
        [rule("r1"), rule("r2", backhaul_entry(HUB_B, SERVICE_B))],
        {BACKHAUL_REF: row_b, OTHER_REF: OTHER_ROW},
    )
    plan = planned(module, edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)]))
    assert rows_of(plan) == [row_b, new_row(SERVICE_A_ID)]
    fresh = qos_at(
        "q2",
        [rule("r1"), rule("r2", backhaul_entry(HUB_B, SERVICE_B))],
        {BACKHAUL_REF: row_b, OTHER_REF: OTHER_WRITERS_ROW},
        enabled=True,
    )

    onto, onto_plan = rebased(module, plan, fresh)

    assert onto.version == "q2"
    assert onto_plan.operation == "update"
    assert onto_plan.refs is not None
    assert onto_plan.refs[OTHER_REF] == OTHER_WRITERS_ROW  # the other writer's
    assert rows_of(onto_plan) == [row_b, new_row(SERVICE_A_ID)]
    (change,) = onto_plan.ref_changes
    assert (change.added, change.row) == (True, new_row(SERVICE_A_ID))
    assert [str(c.path) for c in onto_plan.changes] == [
        str(c.path) for c in plan.changes
    ]
    assert onto_plan.data["serviceRateLimit"]["enabled"] is True


def test_a_row_the_fresh_refs_hold_already_is_sent_once() -> None:
    row_a = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    module = qos_at("q1", [rule("r1"), rule("r2")], {OTHER_REF: OTHER_ROW})
    plan = planned(module, edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)]))
    assert rows_of(plan) == [new_row(SERVICE_A_ID)]
    # Another writer has r2 use the hub too, which gave it its row.
    fresh = qos_at(
        "q2",
        [rule("r1"), rule("r2", backhaul_entry(HUB_A, SERVICE_A))],
        {BACKHAUL_REF: row_a, OTHER_REF: OTHER_ROW},
    )

    onto, onto_plan = rebased(module, plan, fresh)

    assert onto.version == "q2"
    assert onto_plan.operation == "update"
    assert onto_plan.ref_changes == ()
    assert rows_of(onto_plan) == [row_a]


def test_a_removal_of_a_row_that_is_already_gone_sends_no_clash() -> None:
    row_a = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    module = qos_at(
        "q1",
        [rule("r1", backhaul_entry(HUB_A, SERVICE_A))],
        {BACKHAUL_REF: row_a, OTHER_REF: OTHER_ROW},
    )
    plan = planned(module, edited(module, r1=[]))
    assert [c.added for c in plan.ref_changes] == [False]
    fresh = qos_at(
        "q2",
        [rule("r1", backhaul_entry(HUB_A, SERVICE_A))],
        {OTHER_REF: OTHER_ROW},
        enabled=True,
    )

    onto, onto_plan = rebased(module, plan, fresh)

    assert onto.version == "q2"
    assert onto_plan.operation == "update"
    assert onto_plan.ref_changes == ()
    assert onto_plan.refs == {OTHER_REF: OTHER_ROW}
    (rule_data,) = onto_plan.data["segments"][0]["rules"]
    route = rule_data["action"]["edge2EdgeRouteAction"]["routeCfg"]
    assert route["backhaulEdges"] == []
