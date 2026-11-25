"""Helper library for VeloCloud Orchestrator configuration APIs."""

from pyvelo.core import PatchError, add, at, delete, move

from pyvelo_intent.config import (
    ConfigPipeline,
    ConfigState,
    SaveResult,
    ValidationWarning,
    build_default_pipeline,
    confirm_save,
    load_config,
)
from pyvelo_intent.diff import (
    Added,
    Change,
    DiffResult,
    FieldChange,
    Modified,
    Removed,
    Unchanged,
    diff_intents,
)
from pyvelo_intent.errors import (
    ConfigInputError,
    IntentError,
    SaveValidationError,
    StateSpentError,
    UnknownRegistryEntryError,
)

__all__ = [
    "Added",
    "Change",
    "ConfigInputError",
    "ConfigPipeline",
    "ConfigState",
    "DiffResult",
    "FieldChange",
    "IntentError",
    "Modified",
    "PatchError",
    "Removed",
    "SaveResult",
    "SaveValidationError",
    "StateSpentError",
    "Unchanged",
    "UnknownRegistryEntryError",
    "ValidationWarning",
    "add",
    "at",
    "build_default_pipeline",
    "confirm_save",
    "delete",
    "diff_intents",
    "load_config",
    "move",
]
