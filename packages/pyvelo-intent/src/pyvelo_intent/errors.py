"""Typed exceptions raised by the intent layer.

The CLI's ``main()`` is the single place that maps these to process exit
codes. Library code raises them rather than calling ``sys.exit()``.
"""

from pyvelo.config.validation import ValidationError


class IntentError(Exception):
    """Base class for intent-layer errors."""


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
            f"save blocked by {len(self.errors)} validator error(s): {summary}"
        )


class StateSpentError(IntentError):
    """A ``ConfigState`` was used after it wrote to the VCO.

    A write (even one that failed part-way) leaves the state's raw snapshots out
    of date, so a further apply or save from it could overwrite what was just
    written. Load a new state instead.
    """

    def __init__(self) -> None:
        super().__init__(
            "this ConfigState has already written to the VCO; load a new one"
        )
