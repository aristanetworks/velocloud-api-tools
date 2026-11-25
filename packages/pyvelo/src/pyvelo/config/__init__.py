"""Configuration: the pipeline and its state, name->ID resolvers and validation.

``ConfigPipeline`` loads an edge's or a profile's configuration into a
``ConfigState``, applies patches to it and saves it. The names a script needs
for that are importable from here: the pipeline and state types, the patch
helpers (``add``, ``delete``, ``move``, ``at``), what a save hands back, and the
errors a patch or a save raises.
"""

from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ComposedModuleError,
    ConfigConflictError,
    DanglingReferenceError,
    DetachedObjectError,
    FixedListError,
    GroupOwnershipError,
    HubListError,
    LossyFieldError,
    ModuleWriteError,
    RefsDroppedError,
    RefsNotLoadedError,
    SegmentSetError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import ModulePlan
from pyvelo.core import PatchError, PatchErrorKind, add, at, delete, move
from pyvelo.core.identity import (
    HubIdentity,
    HubServiceNotFoundError,
    NetworkServiceIdentity,
    ResolverNotLoadedError,
)
from pyvelo.core.response_check import PlannedChange, ServerChange

from .diff import (
    Added,
    Change,
    DiffResult,
    FieldChange,
    Modified,
    Removed,
    Unchanged,
    diff_intents,
)
from .effective import (
    EffectiveCategory,
    EffectiveComparison,
    EffectiveDifference,
)
from .errors import (
    ConfigInputError,
    IntentError,
    LayerNotSavedError,
    SaveStepError,
    SaveValidationError,
    UnknownRegistryEntryError,
)
from .helpers import confirm_save, load_config
from .hub_steps import HubStep
from .pipeline import ConfigPipeline, build_default_pipeline
from .resolver import ResolutionError, ResolverContext, ResolverRegistry
from .state import (
    ConfigFieldChange,
    ConfigPlan,
    ConfigState,
    SaveProgress,
    SaveResult,
    StepOutcome,
    ValidationWarning,
    save_progress,
)
from .validation import (
    ValidationError,
    ValidationResult,
    Validator,
    ValidatorRegistry,
    validator,
)

__all__ = [
    "Added",
    "Change",
    "ComposedModuleError",
    "ConfigConflictError",
    "ConfigFieldChange",
    "ConfigInputError",
    "ConfigPipeline",
    "ConfigPlan",
    "ConfigState",
    "ConfigurationModuleName",
    "DanglingReferenceError",
    "DetachedObjectError",
    "DiffResult",
    "EffectiveCategory",
    "EffectiveComparison",
    "EffectiveDifference",
    "FieldChange",
    "FixedListError",
    "GroupOwnershipError",
    "HubIdentity",
    "HubListError",
    "HubServiceNotFoundError",
    "HubStep",
    "IntentError",
    "LayerNotSavedError",
    "LossyFieldError",
    "Modified",
    "ModulePlan",
    "ModuleWrite",
    "ModuleWriteError",
    "NetworkServiceIdentity",
    "PatchError",
    "PatchErrorKind",
    "PlannedChange",
    "RefsDroppedError",
    "RefsNotLoadedError",
    "Removed",
    "ResolutionError",
    "ResolverContext",
    "ResolverNotLoadedError",
    "ResolverRegistry",
    "SaveProgress",
    "SaveResult",
    "SaveStepError",
    "SaveValidationError",
    "SegmentSetError",
    "ServerChange",
    "StepOutcome",
    "Unchanged",
    "UnknownRegistryEntryError",
    "ValidationError",
    "ValidationResult",
    "ValidationWarning",
    "Validator",
    "ValidatorRegistry",
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
    "validator",
]
