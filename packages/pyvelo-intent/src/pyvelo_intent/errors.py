"""Typed exceptions raised by the intent layer.

The CLI's ``main()`` is the single place that maps these to process exit
codes. Library code raises them rather than calling ``sys.exit()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast, override

from pyvelo.config.validation import ValidationError


def _rebuild_error(cls: type[IntentError], args: tuple[Any, ...]) -> IntentError:
    """Build an error of *cls* without calling its constructor; its attributes arrive as state."""
    error = cls.__new__(cls)
    error.args = args
    return error


class IntentError(Exception):
    """Base class for intent-layer errors.

    An error is pickled and copied from its class, its ``args`` and its
    attributes (notes included), not by calling the constructor again: a
    constructor may take more than the message, and ``args`` holds only the
    message. As with any exception, ``__cause__``, ``__context__`` and the
    traceback are not carried.
    """

    @override
    def __reduce__(self) -> tuple[Any, ...]:
        attributes = dict(self.__dict__)
        if "__notes__" in attributes:
            # A shallow copy must not add its notes to this error's list.
            attributes["__notes__"] = list(cast(list[str], attributes["__notes__"]))
        return (_rebuild_error, (type(self), self.args), attributes)


class ConfigInputError(IntentError):
    """User-supplied config (CLI args, YAML) is invalid."""


class UnknownRegistryEntryError(LookupError):
    """A registry lookup failed (intent module or object handler)."""


class SaveValidationError(IntentError):
    """Write-time validators rejected a save.

    Carries the structured ``ValidationError`` list from the validator
    framework so callers can inspect individual failures.
    """

    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors: list[ValidationError] = list(errors)
        summary = ", ".join(f"{e.path}: {e.message}" for e in self.errors)
        super().__init__(
            f"save blocked by {len(self.errors)} validator error(s): {summary}. "
            + "Fix them and save again; a save with dry_run=True returns the "
            + "errors without raising"
        )


class LayerNotSavedError(IntentError):
    """The layer of a state that its saves do not write was edited.

    ``layer`` is the layer that was edited (``"profile"`` or ``"edge"``) and
    ``fields`` maps each module's name to the paths edited in it, as text.
    """

    def __init__(
        self,
        layer: str,
        saved: str,
        fields: Mapping[str, Sequence[str]],
        profile_id: int | None = None,
    ) -> None:
        self.layer: str = layer
        self.fields: dict[str, list[str]] = {
            module: list(paths) for module, paths in fields.items()
        }
        edited = "; ".join(
            f"{module} ({', '.join(paths)})" for module, paths in self.fields.items()
        )
        if layer == "profile":
            load = (
                "load_profile" if profile_id is None else f"load_profile({profile_id})"
            )
        else:
            load = "load()"
        super().__init__(
            f"this state saves its {saved} layer only, and its {layer} layer "
            + f"was edited: {edited}. Those edits are not saved. To change the "
            + f"{layer}, load it with {load} and make the edits there. They are "
            + "reported this once: the next plan or save goes ahead without them."
        )
