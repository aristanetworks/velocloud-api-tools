"""Configuration helpers: pipeline, state, aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from .aspect import Aspect, AspectRegistry, AspectSummary
from .helpers import confirm_save, get_vco_credentials, load_config
from .pipeline import ConfigPipeline, build_default_pipeline
from .schema import generate_patch_schema
from .state import ConfigState, SaveResult, ValidationWarning

__all__ = [
    "Aspect",
    "AspectRegistry",
    "AspectSummary",
    "ConfigPipeline",
    "ConfigState",
    "ConfigurationModuleName",
    "SaveResult",
    "ValidationWarning",
    "build_default_pipeline",
    "confirm_save",
    "generate_patch_schema",
    "get_vco_credentials",
    "load_config",
]
