import copy
import uuid
from typing import Any, cast

from pydantic import MISSING
from pyvelo.client.models.configuration.wan.link import WanLinkCosEntry
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import WanConfigurationModule
from pyvelo.core.rebase import AddedItem, UnreadDefault, without_unread_defaults
from pyvelo.testing import make_module_json

LINK_ID = "11111111-1111-4111-8111-111111111111"
READ_ID = "22222222-2222-4222-8222-222222222222"


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
