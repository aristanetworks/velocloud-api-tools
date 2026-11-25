"""The errors of ``pyvelo.config.errors`` keep their class, attributes, notes and message through pickle and copy."""

import copy
import pickle
import uuid
from typing import Any

from pyvelo.config.errors import (
    ConfigInputError,
    IntentError,
    LayerNotSavedError,
    SaveStepError,
    SaveValidationError,
    UnknownRegistryEntryError,
)
from pyvelo.config.hub_steps import HubRef, HubStep
from pyvelo.config.validation import ValidationError


def a_step(action: str = "enable", name: str = "Hub-A") -> HubStep:
    hub = HubRef(
        "edge", uuid.UUID(int=7), name, 31 if action == "enable" else None, None
    )
    return HubStep(action, "backhaul", hub, "Global Segment", 0, 501)  # pyright: ignore[reportArgumentType]


def copies(error: Exception) -> list[Any]:
    return [pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)]


def all_subclasses(cls: type) -> set[type]:
    found: set[type] = set()
    for sub in cls.__subclasses__():
        found |= {sub} | all_subclasses(sub)
    return found


def test_every_error_of_the_module_round_trips() -> None:
    errors: list[Exception] = [
        IntentError("plain"),
        ConfigInputError("bad input"),
        UnknownRegistryEntryError("no such module"),
        SaveValidationError([ValidationError("a.b", "too long", "too_long")]),
        LayerNotSavedError(
            "profile", "edge", {"firewall": ["segments[a].name"]}, profile_id=20
        ),
        SaveStepError(
            "VcoOperationError: boom",
            step=a_step(),
            steps_done=(a_step("disable", "Hub-B"),),
            steps_in_place=(a_step("enable", "Hub-C"),),
            written=("deviceSettings",),
        ),
    ]
    for error in errors:
        error.add_note("a note")
    assert {type(error) for error in errors} == all_subclasses(IntentError) | {
        IntentError,
        UnknownRegistryEntryError,
    }
    for error in errors:
        for copied in copies(error):
            assert type(copied) is type(error)
            assert copied.args == error.args
            assert str(copied) == str(error)
            assert vars(copied) == vars(error)
            assert copied.__notes__ == ["a note"]


def test_the_attributes_of_the_save_errors_are_kept() -> None:
    invalid = SaveValidationError([ValidationError("a.b", "too long", "too_long")])
    layer = LayerNotSavedError("edge", "profile", {"qos": ["rate", "burst"]})
    for copied in copies(invalid):
        assert [(e.path, e.message, e.code) for e in copied.errors] == [
            ("a.b", "too long", "too_long")
        ]
    for copied in copies(layer):
        assert (copied.layer, copied.fields) == ("edge", {"qos": ["rate", "burst"]})


def test_the_attributes_of_a_save_step_error_are_kept() -> None:
    failed, done, in_place = a_step(), a_step("disable", "Hub-B"), a_step("enable", "C")
    error = SaveStepError(
        "VcoOperationError: boom",
        step=failed,
        module_name=None,
        steps_done=[done],
        steps_in_place=[in_place],
        written=["deviceSettings", "QOS"],
    )
    for copied in copies(error):
        assert copied.step == failed
        assert copied.module_name is None
        assert copied.steps_done == (done,)
        assert copied.steps_in_place == (in_place,)
        assert copied.written == ("deviceSettings", "QOS")
        assert str(copied) == str(error)


def test_an_attribute_that_refers_back_to_the_error_survives() -> None:
    error = ConfigInputError("bad")
    error.me = error  # pyright: ignore[reportAttributeAccessIssue]
    for copied in copies(error)[::2]:
        assert copied.me is copied


def test_a_copy_does_not_share_the_error_s_notes() -> None:
    error = ConfigInputError("bad")
    error.add_note("one")
    shallow = copy.copy(error)
    shallow.add_note("two")
    assert error.__notes__ == ["one"]
