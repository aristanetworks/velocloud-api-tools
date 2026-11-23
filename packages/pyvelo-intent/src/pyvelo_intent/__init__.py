"""Helper library for VeloCloud Orchestrator configuration APIs."""

from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    ModuleWriteError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
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
    LayerNotSavedError,
    SaveValidationError,
    UnknownRegistryEntryError,
)

__all__ = [
    "Added",
    "Change",
    "ConfigConflictError",
    "ConfigInputError",
    "ConfigPipeline",
    "ConfigState",
    "DetachedObjectError",
    "DiffResult",
    "FieldChange",
    "IntentError",
    "LayerNotSavedError",
    "Modified",
    "ModuleWriteError",
    "PatchError",
    "Removed",
    "SaveResult",
    "SaveValidationError",
    "Unchanged",
    "UnknownRegistryEntryError",
    "ValidationWarning",
    "WriteNotAppliedError",
    "WriteOutcomeUnknownError",
    "add",
    "at",
    "build_default_pipeline",
    "confirm_save",
    "delete",
    "diff_intents",
    "load_config",
    "move",
]
