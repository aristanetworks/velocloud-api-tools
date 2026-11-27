"""Typed exceptions raised by the intent layer.

The CLI's ``main()`` is the single place that maps these to process exit
codes. Library code raises them rather than calling ``sys.exit()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    python_module_name,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyvelo.config.hub_steps import HubStep
    from pyvelo.config.validation import ValidationError


def _rebuild_error(cls: type[IntentError], args: tuple[Any, ...]) -> IntentError:
    """Build an error of *cls* without calling its constructor.

    Its attributes arrive as state.
    """
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
            attributes["__notes__"] = list(cast("list[str]", attributes["__notes__"]))
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
        """Keep *errors* and name each one's place and message in the message.

        A finding with a module is named as a reference's text names a place
        (``device_settings lan.networks: ...``), one with none by its path.
        """
        self.errors: list[ValidationError] = list(errors)
        summary = ", ".join(
            f"{e.path}: {e.message}"
            if e.module is None
            else f"{e.module} {e.path}: {e.message}"
            for e in self.errors
        )
        super().__init__(
            f"save blocked by {len(self.errors)} validator error(s): {summary}. "
            "Fix them and save again; a save with dry_run=True returns the "
            "errors without raising"
        )


class LayerNotSavedError(IntentError):
    """The layer of a state that its saves do not write was edited.

    ``layer`` is the layer that was edited (``"profile"`` or ``"edge"``) and
    ``fields`` maps each module's python name to the paths edited in it, as
    text: the paths a plan of that layer shows for those edits, so each one,
    with its module, can be given to ``apply`` on a state that saves it.
    """

    def __init__(
        self,
        layer: str,
        saved: str,
        fields: Mapping[str, Sequence[str]],
        profile_id: int | None = None,
    ) -> None:
        """Name the edits to *layer*, which a state saving its *saved* layer drops.

        *profile_id*, when given, goes in the ``load_profile`` call the message
        suggests for a profile edit.
        """
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
            f"was edited: {edited}. Those edits are not saved. To change the "
            f"{layer}, load it with {load} and make the edits there. They are "
            "reported this once: the next plan or save goes ahead without them."
        )


class SaveStepError(IntentError):
    """A save that sends role steps failed after it may have sent one.

    The save of a profile whose edit adds or removes hubs sends the role calls
    (``ConfigPlan.steps``) right before it writes the device settings, and
    nothing it sent is undone when a later request fails. The device settings
    are written first, and the role steps right before them; in a save that
    removes a hub from a list (a plan with a ``disable`` step) they are written
    last, after the other modules, so that a rule stops using a hub before the
    hub loses its role. A write before the steps that fails raises its own
    error, not this one. ``step`` is the step whose call failed (it may have
    reached the VCO), or None when a module write failed after at least one
    step was sent, and ``module_name`` then names that module, by its python
    name (``device_settings``), whichever name the error was made with.
    ``steps_done``
    are the steps sent before the failure and ``steps_in_place`` those found
    already in place and not sent, in the order of the plan; ``written`` are
    the names of the modules stored, in order, as the VCO names them (the
    message names each by its python name). The error is raised ``from``
    the error of the VCO, and ``save_progress(error)`` gives the modules not
    written. The state keeps its edits: a second ``save`` plans the steps
    again, skips those in place and sends the rest. ``revert`` of the device
    settings is no way back: it does not undo a step a save sent, and the
    reverted state plans nothing that mends the role change.
    """

    def __init__(  # noqa: PLR0913 (an error's constructor: the reason, then each field the error reports, by keyword)
        self,
        reason: str,
        *,
        step: HubStep | None = None,
        module_name: str | None = None,
        steps_done: Sequence[HubStep] = (),
        steps_in_place: Sequence[HubStep] = (),
        written: Sequence[str] = (),
    ) -> None:
        """Say where the save stopped, and why (*reason*), and what it sent before."""
        self.step: HubStep | None = step
        self.module_name: str | None = (
            None if module_name is None else python_module_name(module_name)
        )
        self.steps_done: tuple[HubStep, ...] = tuple(steps_done)
        self.steps_in_place: tuple[HubStep, ...] = tuple(steps_in_place)
        self.written: tuple[str, ...] = tuple(written)
        super().__init__(self._message(reason))

    def _message(self, reason: str) -> str:
        if self.step is not None:
            what = (
                f"the save stopped at the step '{self.step.render()}', which "
                f"failed ({reason}); it may have reached the VCO"
            )
        elif self.module_name is not None:
            what = (
                "the save stopped at the write of the module "
                f"{self.module_name}, which failed ({reason})"
            )
        else:
            # A device settings write the version read found stored is done.
            before = (
                ""
                if ConfigurationModuleName.DEVICE_SETTINGS in self.written
                else ", before it wrote the device settings"
            )
            what = (
                f"the save stopped after its steps{before}, at a request that "
                f"failed ({reason})"
            )
        sent = (
            "steps sent: " + "; ".join(step.render() for step in self.steps_done)
            if self.steps_done
            else "no step was sent before it"
        )
        in_place = (
            "; steps found in place and not sent: "
            + "; ".join(step.render() for step in self.steps_in_place)
            if self.steps_in_place
            else ""
        )
        stored = (
            "; modules known to be written: "
            + ", ".join(python_module_name(name) for name in self.written)
            if self.written
            else "; no module is known to be written"
        )
        return (
            f"{what}. At that point {sent}{in_place}{stored}. Nothing is undone: "
            "the roles the sent steps set stay on the VCO, so a hub may have a "
            "role for a list that the device settings do not hold yet. The "
            "state keeps your edits. Save again with the same state: the next "
            "save plans the steps again, skips those already in place, sends "
            "the rest, and then writes the modules that are left. Do not take "
            "the device settings' edits back with `revert`: it does not "
            "undo a step a save sent, so the role change stays on the VCO and "
            "a reverted state plans nothing that mends it."
        )
