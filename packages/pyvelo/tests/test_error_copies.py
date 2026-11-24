"""The errors of ``pyvelo.client.exceptions`` keep their class and attributes through pickle and copy."""

import copy
import pickle
import uuid
from typing import Any

import pytest
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DanglingReferenceError,
    DetachedObjectError,
    FixedListError,
    GroupOwnershipError,
    HubListError,
    LayerUnknownError,
    LossyFieldError,
    ModuleWriteError,
    RefsDroppedError,
    RefsNotLoadedError,
    SegmentSetError,
    UnboundError,
    VcoApiError,
    VcoClientNotInitializedError,
    VcoCredentialsError,
    VcoEmptyResultError,
    VcoError,
    VcoHttpError,
    VcoOperationError,
    VcoRateLimitError,
    VcoUserTypeUnknownError,
    VcoWebSocketConnectionError,
    VcoWebSocketDisconnectedError,
    VcoWebSocketError,
    VcoWebSocketTimeoutError,
    VcoWebSocketValidationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.module_check import ModuleWrite, check_module_write
from pyvelo.client.module_write import plan_module
from pyvelo.core.identity import (
    HubService,
    HubServiceNotFoundError,
    NetworkServiceIdentity,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.layers import Edge
from pyvelo.core.patch import PatchError
from pyvelo.core.rebase import Key, Path
from pyvelo.core.references import Reference
from pyvelo.testing import as_stored

from .write_helpers import fw


def lost_write() -> ConfigConflictError:
    """The conflict of a write that stored nothing, with one earlier write of the same save."""
    module = fw({"segments": [], "firewall_enabled": True}, layer=Edge)
    module.data.firewall_enabled = False
    plan = plan_module(module)
    stored = as_stored(module, {"segments": [], "firewall_enabled": True})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored)
    exc.value.completed = (ModuleWrite("QOS", "update", stored, plan),)
    return exc.value


def copies(error: Exception) -> list[Any]:
    return [pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)]


def test_a_conflict_survives_pickling_and_copying():
    error = lost_write()
    for copied in copies(error):
        assert type(copied) is ConfigConflictError
        assert str(copied) == str(error)
        assert (copied.detail, copied.stated) == (error.detail, False)
        assert (copied.module_id, copied.module_name, copied.version) == (
            1,
            "firewall",
            "v1",
        )
        (lost,) = copied.lost_edits
        assert (str(lost.path), lost.as_read, lost.intended, lost.stored) == (
            "firewall_enabled",
            True,
            False,
            True,
        )
        assert copied.plan == error.plan
        assert copied.module is not None
        assert copied.module.raw_data == {"segments": [], "firewall_enabled": True}
        assert [write.name for write in copied.completed] == ["QOS"]


def test_the_other_write_errors_survive_pickling():
    module = fw({"segments": [], "future": 1}, layer=Edge)
    plan = plan_module(module, {"segments": [], "future": 2})
    stored = as_stored(module, {"segments": [], "future": 3})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    unknown = WriteOutcomeUnknownError(
        1,
        "v1",
        "its configuration does not hold the module",
        answer="counted",
        module_name="firewall",
        plan=plan,
    )
    for error in (exc.value, unknown):
        loaded = pickle.loads(pickle.dumps(error))
        assert type(loaded) is type(error)
        assert str(loaded) == str(error)
    again = pickle.loads(pickle.dumps(unknown))
    assert (again.answer, again.detail, again.plan) == (
        "counted",
        "its configuration does not hold the module",
        plan,
    )


def test_a_fixed_list_error_keeps_its_module_paths_and_message_through_copies():
    paths = (Path(("segments", Key("Guest"), "defaults")),)
    error = FixedListError("QOS", [(paths[0], "the list is gone")])
    for copied in copies(error):
        assert type(copied) is FixedListError
        assert (copied.module_name, copied.paths) == ("QOS", paths)
        assert str(copied) == str(error)


def test_a_dangling_reference_error_keeps_its_references_and_message_through_copies():
    references = (
        Reference(
            "deviceSettings",
            Path(("lan", "networks", Key(100), "segment_id")),
            7,
            "segment",
            None,
        ),
        Reference(
            "QOS", Path(("segments", Key("Guest"))), uuid.UUID(int=5), "backhaul_hub", 2
        ),
    )
    error = DanglingReferenceError(references)
    for copied in copies(error):
        assert type(copied) is DanglingReferenceError
        assert copied.references == references
        assert str(copied) == str(error)


def test_a_hub_list_error_keeps_its_attributes_and_message_through_copies():
    error = HubListError(
        "a hub needs its role",
        segment="Global Segment",
        lists=("vpn.backHaulEdges", "vpn.edgeToEdgeHub.vpnHubs"),
        hubs=("Hub-A",),
        edges=((31, "Branch-1"),),
    )
    for copied in copies(error):
        assert type(copied) is HubListError
        assert (copied.segment, copied.lists, copied.hubs, copied.edges) == (
            "Global Segment",
            ("vpn.backHaulEdges", "vpn.edgeToEdgeHub.vpnHubs"),
            ("Hub-A",),
            ((31, "Branch-1"),),
        )
        assert str(copied) == str(error)


def test_a_segment_set_error_keeps_its_attributes_and_message_through_copies():
    error = SegmentSetError(
        "one module cannot add a segment",
        module_name="QOS",
        segments=("Guest",),
        modules=("QOS",),
    )
    for copied in copies(error):
        assert type(copied) is SegmentSetError
        assert (copied.module_name, copied.segments, copied.modules) == (
            "QOS",
            ("Guest",),
            ("QOS",),
        )
        assert str(copied) == str(error)


def test_errors_built_from_several_values_survive_pickling():
    path = Path(("segments", 0, "outbound"))
    operation = VcoOperationError("update", "boom", code=5, data={"x": 1})
    lossy = LossyFieldError("firewall", [path], [path])
    http = VcoHttpError(502, "bad gateway")
    detached = DetachedObjectError("changed", (path,))
    for error in (operation, lossy, http, detached):
        for copied in copies(error):
            assert type(copied) is type(error)
            assert str(copied) == str(error)
            assert vars(copied) == vars(error)


def test_a_refs_not_loaded_error_keeps_its_module_paths_and_message_through_copies():
    paths = (Path(("segments", 0, "dns")),)
    for error in (
        RefsNotLoadedError("deviceSettings", paths),
        RefsNotLoadedError("QOS"),
    ):
        for copied in copies(error):
            assert type(copied) is RefsNotLoadedError
            assert (copied.module_name, copied.paths) == (
                error.module_name,
                error.paths,
            )
            assert str(copied) == str(error)


def test_a_refs_dropped_error_keeps_the_refs_as_read_through_copies():
    plan = plan_module(fw({"segments": []}, layer=Edge))
    error = RefsDroppedError(
        1,
        "v1",
        module_name="firewall",
        plan=plan,
        module=fw({}, layer=Edge),
        lost_edits=[],
        refs_as_read={"a": [{"logicalId": "x"}]},
    )
    for copied in copies(error):
        assert type(copied) is RefsDroppedError
        assert copied.refs_as_read == {"a": [{"logicalId": "x"}]}
        assert str(copied) == str(error)


def test_notes_and_attributes_set_after_the_error_was_raised_survive():
    error = lost_write()
    error.add_note("saved: QOS")
    error.attached = {"failed": "firewall"}  # pyright: ignore[reportAttributeAccessIssue]
    for copied in copies(error):
        assert copied.__notes__ == ["saved: QOS"]
        assert copied.attached == {"failed": "firewall"}
        assert str(copied) == str(error)


def test_a_copy_does_not_share_the_error_s_notes_or_attributes():
    error = VcoOperationError("update", "boom", data={"x": [1]})
    error.add_note("one")
    shallow = copy.copy(error)
    shallow.code = 9
    shallow.add_note("two")
    assert error.code is None
    assert error.__notes__ == ["one"]
    deep = copy.deepcopy(error)
    deep.data["x"].append(2)
    assert error.data == {"x": [1]}


def all_subclasses(cls: type) -> set[type]:
    found: set[type] = set()
    for sub in cls.__subclasses__():
        found |= {sub} | all_subclasses(sub)
    return found


def test_every_error_of_the_module_round_trips():
    path = Path(("segments", 0))
    plan = plan_module(fw({"segments": []}, layer=Edge))
    errors: list[VcoError] = [
        VcoError("plain"),
        VcoClientNotInitializedError(),
        VcoApiError(-32000, "denied", data={"error": [{"code": "X", "path": "a"}]}),
        VcoEmptyResultError("enterprise/get"),
        VcoOperationError("update", "boom", code=5, data={"x": 1}),
        VcoHttpError(502, "bad gateway"),
        VcoRateLimitError(3),
        VcoWebSocketError("closed"),
        VcoWebSocketConnectionError("no handshake"),
        VcoWebSocketTimeoutError("slow", timeout=2.5),
        VcoWebSocketDisconnectedError("gone"),
        VcoWebSocketValidationError("rejected"),
        VcoUserTypeUnknownError(),
        VcoCredentialsError("VCO_HOST is not set"),
        LossyFieldError("firewall", [path], [path]),
        ConfigConflictError(1, "v1", "stale", module_name="firewall", plan=plan),
        ConfigConflictError(
            1,
            "v1",
            "stale",
            stated=False,
            sent=False,
            module_name="firewall",
            plan=plan,
        ),
        WriteNotAppliedError(
            1,
            "v1",
            module_name="firewall",
            plan=plan,
            module=fw({}, layer=Edge),
            lost_edits=[],
        ),
        WriteOutcomeUnknownError(
            1, "v1", "unreadable", answer="row", module_name="firewall", plan=plan
        ),
        ModuleWriteError(1, "v1"),
        UnboundError("not bound"),
        LayerUnknownError(
            "the row has no layer", module_id=1, answer={"id": 1, "data": {}}
        ),
        GroupOwnershipError(
            "one member", group="stateful", field="stateful_firewall_enabled"
        ),
        DetachedObjectError("changed", (path,)),
        RefsNotLoadedError("deviceSettings", (path,)),
        FixedListError("QOS", [(path, "the list is gone")]),
        SegmentSetError(
            "no",
            module_name="QOS",
            segments=("Guest",),
            modules=("QOS",),
        ),
        HubListError(
            "no",
            segment="Global Segment",
            lists=("vpn.backHaulEdges",),
            hubs=("Hub-A",),
            edges=((31, "Hub-A"),),
        ),
        DanglingReferenceError([Reference("deviceSettings", path, 7, "segment", None)]),
        RefsDroppedError(
            1,
            "v1",
            module_name="firewall",
            plan=plan,
            module=fw({}, layer=Edge),
            lost_edits=[],
            refs_as_read={"a": []},
        ),
    ]
    # Test support may subclass an error (a live test's refusal); every
    # subclass the libraries define is listed here, whichever package has it.
    library = {
        sub for sub in all_subclasses(VcoError) if sub.__module__ != "live_support"
    }
    assert {type(error) for error in errors} == library | {VcoError}
    for error in errors:
        for copied in copies(error):
            assert type(copied) is type(error)
            assert copied.args == error.args
            assert str(copied) == str(error)
            assert vars(copied).keys() == vars(error).keys()


def test_an_attribute_that_refers_back_to_the_error_survives():
    error = VcoOperationError("update", "boom", code=5)
    error.me = error  # pyright: ignore[reportAttributeAccessIssue]
    error.add_note("one")
    loaded, _, deep = copies(error)
    for copied in (loaded, deep):
        assert copied.me is copied
        assert type(copied) is VcoOperationError
        assert copied.__notes__ == ["one"]
    assert deep is not error
    shallow = copy.copy(error)
    assert shallow.me is error  # pyright: ignore[reportAttributeAccessIssue]
    shallow.add_note("two")
    assert error.__notes__ == ["one"]


def test_the_stored_parts_of_a_write_error_survive_a_deep_copy():
    parts = {Path(("segments", 0)): 0, Path(("segments", 1)): Key("b")}
    error = ConfigConflictError(
        1, "v1", "stale", module_name="firewall", stored_parts=parts
    )
    for copied in copies(error):
        assert copied.stored_parts == parts
    assert copy.deepcopy(error).stored_parts is not error.stored_parts


def test_a_resolution_error_keeps_its_message_and_attributes_through_copies():
    error = ResolutionError(
        name="Corp", identity_type=SegmentIdentity, candidates=["Corporate", "Core"]
    )
    error.add_note("looked in the cache")
    for copied in copies(error):
        assert type(copied) is ResolutionError
        assert copied.args == error.args
        assert str(copied) == str(error) == error.args[0]
        assert (copied.name, copied.identity_type, copied.candidates) == (
            "Corp",
            SegmentIdentity,
            ["Corporate", "Core"],
        )
        assert copied.__notes__ == ["looked in the cache"]
    assert copy.copy(error).candidates is error.candidates
    assert copy.deepcopy(error).candidates is not error.candidates
    shallow = copy.copy(error)
    shallow.add_note("two")
    assert error.__notes__ == ["looked in the cache"]


def test_a_resolver_not_loaded_error_keeps_its_class_message_and_identity_type_through_copies():
    error = ResolverNotLoadedError(NetworkServiceIdentity)
    error.add_note("while resolving a ref")
    for copied in copies(error):
        assert type(copied) is ResolverNotLoadedError
        assert copied.args == error.args
        assert str(copied) == str(error)
        assert "load_resolvers(state, NetworkServiceIdentity)" in str(copied)
        assert copied.identity_type is NetworkServiceIdentity
        assert (copied.name, copied.candidates) == ("", [])
        assert copied.__notes__ == ["while resolving a ref"]


def test_a_hub_service_not_found_error_keeps_its_class_message_and_attributes_through_copies():
    error = HubServiceNotFoundError(
        hub="Hub-A",
        segment="Global Segment",
        path='segments["Global Segment"].rules["r1"].action.route_cfg',
    )
    error.add_note("while planning QOS")
    for copied in copies(error):
        assert type(copied) is HubServiceNotFoundError
        assert copied.args == error.args
        assert str(copied) == str(error)
        assert (copied.hub, copied.segment, copied.path) == (
            "Hub-A",
            "Global Segment",
            'segments["Global Segment"].rules["r1"].action.route_cfg',
        )
        assert (copied.name, copied.identity_type, copied.candidates) == (
            "Hub-A",
            HubService,
            [],
        )
        assert copied.__notes__ == ["while planning QOS"]
    assert isinstance(error, ResolutionError)


def test_a_resolution_error_given_its_own_message_keeps_it():
    error = ResolutionError(
        name="x", identity_type="thing", candidates=[], message="no such thing"
    )
    for copied in copies(error):
        assert str(copied) == "no such thing"
        assert copied.identity_type == "thing"


def test_a_patch_error_keeps_its_path_kind_and_notes_through_copies():
    error = PatchError("no such field", Path(("segments", 0, "name")), "unknown_field")
    error.add_note("in the second patch")
    for copied in copies(error):
        assert type(copied) is PatchError
        assert copied.args == error.args
        assert str(copied) == str(error)
        assert (copied.message, copied.path, copied.kind) == (
            "no such field",
            Path(("segments", 0, "name")),
            "unknown_field",
        )
        assert copied.__notes__ == ["in the second patch"]


def test_a_dangling_reference_error_for_an_edge_keeps_that_through_copies():
    reference = Reference("QOS", Path(("x",)), uuid.UUID(int=5), "backhaul_hub", 2)
    error = DanglingReferenceError([reference], on_edge=True)
    for copied in copies(error):
        assert copied.on_edge is True
        assert str(copied) == str(error)


def test_a_hub_service_message_names_the_hub_by_logical_id_when_it_has_no_name():
    logical_id = str(uuid.UUID(int=7))
    named = HubServiceNotFoundError(hub="Hub-A", segment="Global Segment", path="p")
    unnamed = HubServiceNotFoundError(
        hub=logical_id, segment="Global Segment", path="p"
    )

    assert '"name": "Hub-A"' in str(named)
    assert '"logicalId"' not in str(named)
    assert f'"logicalId": "{logical_id}"' in str(unnamed)
    assert '"name"' not in str(unnamed)
