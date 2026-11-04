"""Aspect system: focused projections into the configuration tree.

Provides:
- Aspect: defines a named projection (name, module, path, tags, segment_scoped)
- AspectSummary: lightweight listing view
- AspectRegistry: register, list, search, get aspects
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field

from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.core.base import VcoModel

from pyvelo_intent.config.schema import (
    generate_patch_schema,
    resolve_model_at_path,
)
from pyvelo_intent.errors import UnknownRegistryEntryError


@dataclass(frozen=True)
class Aspect:
    """A named projection into the configuration tree."""

    name: str
    module: ConfigurationModuleName
    path: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    examples: list[dict[str, object]] | None = None
    segment_scoped: bool = False


@dataclass(frozen=True)
class AspectSummary:
    """Lightweight listing view for an aspect."""

    name: str
    description: str
    tags: list[str]


class AspectRegistry:
    """Registry for aspect definitions with search and lookup."""

    def __init__(self) -> None:
        self._aspects: dict[str, Aspect] = {}

    def register(self, aspect: Aspect) -> None:
        """Register an aspect, overwriting any existing aspect with the same name."""
        self._aspects[aspect.name] = aspect

    def list_aspects(self) -> list[AspectSummary]:
        """Return summaries of all registered aspects."""
        return [
            AspectSummary(name=a.name, description=a.description, tags=a.tags)
            for a in self._aspects.values()
        ]

    def search(self, query: str) -> list[AspectSummary]:
        """Search aspects by matching query against name, description, and tags."""
        query_lower = query.lower()
        results: list[AspectSummary] = []
        for aspect in self._aspects.values():
            if (
                query_lower in aspect.name.lower()
                or query_lower in aspect.description.lower()
                or any(query_lower in tag.lower() for tag in aspect.tags)
            ):
                results.append(
                    AspectSummary(
                        name=aspect.name,
                        description=aspect.description,
                        tags=aspect.tags,
                    )
                )
        return results

    def get(self, name: str) -> Aspect:
        """Look up an aspect by name. Raises ``UnknownRegistryEntryError`` if not found."""
        try:
            return self._aspects[name]
        except KeyError:
            available = ", ".join(sorted(self._aspects)) or "(none)"
            raise UnknownRegistryEntryError(
                f"Unknown aspect: {name!r}. Available: {available}"
            ) from None

    def get_patch_schema(
        self,
        name: str,
        *,
        module_models: Mapping[ConfigurationModuleName, type[VcoModel]],
    ) -> dict[str, object]:
        """Generate a patch JSON Schema for the named aspect.

        module_models maps module names to their data model types, e.g.:
        {"deviceSettings": DeviceSettingsData, "firewall": FirewallData}
        """
        aspect = self.get(name)
        model_type = module_models.get(aspect.module)
        if model_type is None:
            raise ValueError(f"No model registered for module {aspect.module!r}")
        return generate_patch_schema(model_type, aspect.path)

    def get_read_schema(
        self,
        name: str,
        *,
        module_models: Mapping[ConfigurationModuleName, type[VcoModel]],
    ) -> dict[str, object]:
        """Generate a read JSON Schema for the named aspect.

        Same as patch schema but without the patch-specific transforms
        (all properties remain required per the model definition).
        """
        aspect = self.get(name)
        model_type = module_models.get(aspect.module)
        if model_type is None:
            raise ValueError(f"No model registered for module {aspect.module!r}")
        target_type = resolve_model_at_path(model_type, aspect.path)
        return copy.deepcopy(target_type.model_json_schema())
