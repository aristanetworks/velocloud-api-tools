"""Helper library for VeloCloud Orchestrator configuration APIs."""

from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    LossyFieldError,
    ModuleWriteError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.core import PatchError, PatchErrorKind, add, at, delete, move
from pyvelo.core.identity import ResolutionError
from pyvelo.core.response_check import PlannedChange, ServerChange

from pyvelo_intent.config import (
    ConfigPipeline,
    ConfigPlan,
    ConfigState,
    SaveProgress,
    SaveResult,
    ValidationWarning,
    build_default_pipeline,
    confirm_save,
    load_config,
    save_progress,
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
    "ConfigPlan",
    "ConfigState",
    "DetachedObjectError",
    "DiffResult",
    "FieldChange",
    "IntentError",
    "LayerNotSavedError",
    "LossyFieldError",
    "Modified",
    "ModuleWriteError",
    "PatchError",
    "PatchErrorKind",
    "PlannedChange",
    "Removed",
    "ResolutionError",
    "SaveProgress",
    "SaveResult",
    "SaveValidationError",
    "ServerChange",
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
    "save_progress",
]
