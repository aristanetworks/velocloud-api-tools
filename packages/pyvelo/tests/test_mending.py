import dataclasses
import json

from pyvelo.core.mending import MendingPatch, backhaul_hub_mending

HUB = {"_op": "add", "name": "Hub-A"}


def test_the_patch_adds_the_hub_to_both_lists_and_turns_on_what_they_need() -> None:
    mending = backhaul_hub_mending("Guest", "Hub-A")

    assert mending == MendingPatch(
        kind="add_member",
        does="Make 'Hub-A' a backhaul hub of segment 'Guest'",
        target_type="profile",
        module="device_settings",
        patch={
            "segments": {
                "Guest": {
                    "vpn": {
                        "enabled": True,
                        "hub_edges": {"enabled": True, "vpn_hubs": {"+": HUB}},
                        "backhaul_edges": {"+": HUB},
                    }
                }
            }
        },
    )
    assert (mending.segment, mending.path) == (None, None)


def test_a_hub_already_in_the_hub_list_is_added_to_the_backhaul_list_only() -> None:
    mending = backhaul_hub_mending("Guest", "Hub-A", in_hub_list=True)

    assert mending.patch == {
        "segments": {"Guest": {"vpn": {"enabled": True, "backhaul_edges": {"+": HUB}}}}
    }


def test_a_hub_with_no_name_is_given_by_its_logical_id() -> None:
    mending = backhaul_hub_mending("Guest", "0-0-0-0-7", by_logical_id=True)

    assert json.dumps({"_op": "add", "logical_id": "0-0-0-0-7"}) in json.dumps(
        mending.patch
    )
    assert '"name"' not in json.dumps(mending.patch)


def test_a_mending_patch_is_json() -> None:
    as_json = dataclasses.asdict(backhaul_hub_mending("Guest", "Hub-A"))

    assert json.loads(json.dumps(as_json)) == as_json
