"""The errors of ``pyvelo.client.exceptions`` keep their class and attributes through pickle and copy."""

import copy
import pickle
from typing import Any

import pytest
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    LossyFieldError,
    ModuleWriteError,
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
from pyvelo.core.identity import ResolutionError, SegmentIdentity
from pyvelo.core.patch import PatchError
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import as_stored

from .write_helpers import fw


def lost_write() -> ConfigConflictError:
    """The conflict of a write that stored nothing, with one earlier write of the same save."""
    module = fw({"segments": [], "firewall_enabled": True})
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
    module = fw({"segments": [], "future": 1})
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
    plan = plan_module(fw({"segments": []}))
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
        WriteNotAppliedError(
            1, "v1", module_name="firewall", plan=plan, module=fw({}), lost_edits=[]
        ),
        WriteOutcomeUnknownError(
            1, "v1", "unreadable", answer="row", module_name="firewall", plan=plan
        ),
        ModuleWriteError(1, "v1"),
        UnboundError("not bound"),
        DetachedObjectError("changed", (path,)),
    ]
    assert {type(error) for error in errors} == all_subclasses(VcoError) | {VcoError}
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
