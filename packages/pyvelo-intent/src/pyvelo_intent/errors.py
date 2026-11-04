"""Typed exceptions raised by the intent layer.

The CLI's ``main()`` is the single place that maps these to process exit
codes. Library code raises them rather than calling ``sys.exit()``.
"""

from pyvelo.config.validation import ValidationError


class IntentError(Exception):
    """Base class for intent-layer errors."""


class MissingEnvVarError(IntentError):
    """Required environment variable is missing or empty."""


class ConfigInputError(IntentError):
    """User-supplied config (CLI args, YAML, env file path) is invalid."""


class UnknownRegistryEntryError(LookupError):
    """A registry lookup failed (intent module, object handler, or aspect)."""


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
