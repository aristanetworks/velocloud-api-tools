"""Configuration framework: name->ID resolvers and validation framework.

Workflow-shaped pieces (pipeline, state, aspects) live in the
``pyvelo_intent.config`` package.
"""

from .resolver import ResolutionError, ResolverContext, ResolverRegistry
from .validation import (
    ValidationError,
    ValidationResult,
    Validator,
    ValidatorRegistry,
    validator,
)

__all__ = [
    "ResolutionError",
    "ResolverContext",
    "ResolverRegistry",
    "ValidationError",
    "ValidationResult",
    "Validator",
    "ValidatorRegistry",
    "validator",
]
