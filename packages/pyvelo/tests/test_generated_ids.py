import copy
import uuid
from typing import Any, cast

from pydantic import MISSING
from pyvelo.client.models.configuration.wan.link import WanLinkCosEntry
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import (
    QosConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.core import ConfigContext, add
from pyvelo.core.layers import Edge
from pyvelo.core.rebase import AddedItem, UnreadDefault, without_unread_defaults
from pyvelo.testing import make_module_json

LINK_ID = "11111111-1111-4111-8111-111111111111"
READ_ID = "22222222-2222-4222-8222-222222222222"
SEGMENT = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": "00000000-0000-4000-8000-000000000000",
}
ACTION = {
    "edge2EdgeRouteAction": {},
    "edge2DataCenterRouteAction": {},
    "edge2CloudRouteAction": {},
    "QoS": {},
}


def wan(*classes: dict[str, Any]) -> WanConfigurationModule:
    link = {
        "type": "WIRED",
        "name": "l1",
        "logicalId": "x",
        "internalId": LINK_ID,
        "classesOfService": {"classesOfService": list(classes)},
    }
    row = make_module_json("WAN", {"links": [copy.deepcopy(link)]})
    return WanConfigurationModule.model_validate(row)


def entries(mod: WanConfigurationModule) -> list[WanLinkCosEntry]:
    links = mod.data.links
    assert links is not MISSING
    return links[0].classes_of_service.classes_of_service


def sent(plan: ModulePlan) -> list[dict[str, Any]]:
    return plan.data["links"][0]["classesOfService"]["classesOfService"]


def test_a_class_read_without_an_id_is_sent_without_one() -> None:
    mod = wan({"dscpTags": ["AF11"]}, {"dscpTags": ["AF12"]})
    entries(mod)[0].policing = True
    first, second = plan_module(mod), plan_module(mod)
    assert ["id" in entry for entry in sent(first)] == [False, False]
    assert sent(first)[0]["policing"] is True
    assert sent(first) == sent(second)


def test_a_class_the_caller_adds_is_sent_with_its_own_uuid() -> None:
    mod = wan({"dscpTags": ["AF11"]}, {"dscpTags": ["AF12"]})
    new = WanLinkCosEntry.model_validate({"dscpTags": ["AF13"]})
    entries(mod).append(new)
    first, second = plan_module(mod), plan_module(mod)
    assert ["id" in entry for entry in sent(first)] == [False, False, True]
    assert sent(first)[2]["id"] == str(new.id) == sent(second)[2]["id"]
    # A UUID of version 4, as the UI makes.
    assert uuid.UUID(sent(first)[2]["id"]).version == 4


def test_a_class_read_with_its_id_keeps_it() -> None:
    """An ID that was read is the entry's; the entry beside it still has none."""
    mod = wan({"dscpTags": ["AF11"], "id": READ_ID}, {"dscpTags": ["AF12"]})
    assert plan_module(mod).operation == "none"
    entries(mod)[1].policing = True
    plan = plan_module(mod)
    assert [entry.get("id") for entry in sent(plan)] == [READ_ID, None]


def test_without_unread_defaults_removes_them_at_any_depth() -> None:
    made = UnreadDefault("made-up")
    document = {"a": [{"id": made, "n": 1}, {"id": "read"}], "b": {"id": made}}
    assert without_unread_defaults(document) == {
        "a": [{"n": 1}, {"id": "read"}],
        "b": {},
    }
    assert document["b"] == {"id": "made-up"}
    (kept,) = cast(
        "list[object]", without_unread_defaults([AddedItem({"id": made, "n": 2})])
    )
    assert type(kept) is AddedItem
    assert kept == {"n": 2}


# --- a QoS list whose rules cannot be told apart ------------------------------


def qos(*names: str) -> QosConfigurationModule[Edge]:
    rules = [{"name": name, "match": {}, "action": ACTION} for name in names]
    raw = {"segments": [{"segment": SEGMENT, "rules": rules}]}
    return QosConfigurationModule[Edge].model_validate(
        make_module_json("QOS", copy.deepcopy(raw))
    )


def add_rule(mod: QosConfigurationModule[Edge], name: str, **place: Any) -> None:
    body = {"name": name, "match": {}, "action": copy.deepcopy(ACTION)}
    patch = {"segments": {"Global Segment": {"rules": {name: add(body, **place)}}}}
    _ = mod.data.merge_patch(patch)


def rule_ids(mod: QosConfigurationModule[Edge]) -> list[tuple[str, str | None]]:
    """Each rule sent, with the ``ruleLogicalId`` sent for it; the same in two plans."""
    first, second = (
        plan_module(mod, level=ConfigContext.EDGE).data["segments"][0]["rules"]
        for _ in range(2)
    )
    assert first == second
    return [(rule["name"], rule.get("ruleLogicalId")) for rule in first]


def model_id(mod: QosConfigurationModule[Edge], name: str) -> str:
    (rule,) = (r for r in mod.data.segments[0].rules if r.name == name)
    return str(rule.rule_logical_id)


def test_a_rule_added_after_a_deletion_among_twins_keeps_its_own_uuid() -> None:
    mod = qos("dup", "dup")
    del mod.data.segments[0].rules[0]
    add_rule(mod, "new")
    assert rule_ids(mod) == [("dup", None), ("new", model_id(mod, "new"))]


def test_rules_read_behind_a_rule_added_first_are_sent_without_ids() -> None:
    mod = qos("dup", "dup")
    add_rule(mod, "new", first=True)
    assert rule_ids(mod) == [
        ("new", model_id(mod, "new")),
        ("dup", None),
        ("dup", None),
    ]


def test_rules_read_around_rules_added_in_the_middle_are_sent_without_ids() -> None:
    mod = qos("dup", "dup", "r3")
    add_rule(mod, "new", before="r3")
    add_rule(mod, "last", last=True)
    assert rule_ids(mod) == [
        ("dup", None),
        ("dup", None),
        ("new", model_id(mod, "new")),
        ("r3", None),
        ("last", model_id(mod, "last")),
    ]
