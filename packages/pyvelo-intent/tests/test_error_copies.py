"""The errors of ``pyvelo_intent.errors`` keep their class, attributes, notes and message through pickle and copy."""

import copy
import pickle
from typing import Any

from pyvelo.config.validation import ValidationError
from pyvelo_intent.errors import (
    ConfigInputError,
    IntentError,
    LayerNotSavedError,
    SaveValidationError,
    UnknownRegistryEntryError,
)


def copies(error: Exception) -> list[Any]:
    return [pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)]


def all_subclasses(cls: type) -> set[type]:
    found: set[type] = set()
    for sub in cls.__subclasses__():
        found |= {sub} | all_subclasses(sub)
    return found


def test_every_error_of_the_module_round_trips():
    errors: list[Exception] = [
        IntentError("plain"),
        ConfigInputError("bad input"),
        UnknownRegistryEntryError("no such module"),
        SaveValidationError([ValidationError("a.b", "too long", "too_long")]),
        LayerNotSavedError(
            "profile", "edge", {"firewall": ["segments[a].name"]}, profile_id=20
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


def test_the_attributes_of_the_save_errors_are_kept():
    invalid = SaveValidationError([ValidationError("a.b", "too long", "too_long")])
    layer = LayerNotSavedError("edge", "profile", {"qos": ["rate", "burst"]})
    for copied in copies(invalid):
        assert [(e.path, e.message, e.code) for e in copied.errors] == [
            ("a.b", "too long", "too_long")
        ]
    for copied in copies(layer):
        assert (copied.layer, copied.fields) == ("edge", {"qos": ["rate", "burst"]})


def test_an_attribute_that_refers_back_to_the_error_survives():
    error = ConfigInputError("bad")
    error.me = error  # pyright: ignore[reportAttributeAccessIssue]
    for copied in copies(error)[::2]:
        assert copied.me is copied


def test_a_copy_does_not_share_the_error_s_notes():
    error = ConfigInputError("bad")
    error.add_note("one")
    shallow = copy.copy(error)
    shallow.add_note("two")
    assert error.__notes__ == ["one"]
