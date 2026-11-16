"""ConfigPipeline: orchestration layer for load → apply → save configuration workflow."""

import copy
from typing import Any, cast

from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    DeviceSettingsConfigurationModule,
    DeviceSettingsModuleUpdate,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    FirewallModuleUpdate,
    GetConfiguration,
    InsertFirewallConfigurationModule,
    InsertQosConfigurationModule,
    QosConfigurationModule,
    QosModuleUpdate,
    UpdateConfigurationModule,
    WanConfigurationModule,
    WanModuleUpdate,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.validation import ValidationError as ValidatorError
from pyvelo.config.validation import ValidationResult, ValidatorRegistry
from pyvelo.core.base import VcoModel
from pyvelo.core.identity import EdgeName, ProfileName

from pyvelo_intent.config.aspect import Aspect, AspectRegistry, AspectSummary
from pyvelo_intent.config.aspect_query import (
    get_aspect_data,
    get_aspect_parent,
    navigate_to_base,
)
from pyvelo_intent.config.aspects import register_default_aspects
from pyvelo_intent.config.module_names import (
    resolve_module_name as _resolve_module_name,
)
from pyvelo_intent.config.state import ConfigState, SaveResult, ValidationWarning


class ConfigPipeline:
    """Manages configuration load, merge, resolve, validate, save."""

    def __init__(
        self,
        client: VcoClientProtocol | None,
        resolvers: ResolverRegistry | None = None,
        aspect_registry: AspectRegistry | None = None,
        validator_registry: ValidatorRegistry | None = None,
    ) -> None:
        self._client: VcoClientProtocol | None = client
        self._resolvers: ResolverRegistry = resolvers or ResolverRegistry()
        self._aspect_registry: AspectRegistry = aspect_registry or AspectRegistry()
        self._validators: ValidatorRegistry = validator_registry or ValidatorRegistry()

    @property
    def client(self) -> VcoClientProtocol:
        """The VCO client used by this pipeline."""
        if self._client is None:
            raise RuntimeError(
                "A VCO client is required for network-backed pipeline operations"
            )
        return self._client

    @property
    def resolvers(self) -> ResolverRegistry:
        """The resolver registry used by this pipeline."""
        return self._resolvers

    async def load(
        self,
        edge: int | str,
        *,
        enterprise_id: int | None = None,
        validate: bool = False,
    ) -> ConfigState:
        """Fetch edge config + profile config (template).

        If edge is a string, resolves by name via EdgeResolver.
        Takes a snapshot of the loaded state for diff-on-save.
        """
        edge_id: int

        if isinstance(edge, str):
            # Resolve edge name to ID
            ctx = ResolverContext(enterprise_id=enterprise_id)
            self._resolvers.set_context(ctx)
            edge_resolver = self._resolvers.get_resolver(EdgeResolver)
            await edge_resolver.load(ctx)

            identity = await self._resolvers.resolve(EdgeName(edge))
            edge_id = identity.id
        else:
            edge_id = edge

        # Fetch edge configuration stack: [edge_specific, profile]
        request = GetEdgeConfigurationStack(edge_id=edge_id)
        if enterprise_id is not None:
            request.enterprise_id = enterprise_id

        stack = await self.client.get_edge_configuration_stack(request)
        edge_config: EdgeConfiguration = stack[0]
        profile_config: EnterpriseConfigurationProfile = stack[1]

        # Set resolver context from loaded state
        resolver_context = ResolverContext(
            enterprise_id=enterprise_id,
            edge_id=edge_id,
            configuration_id=(
                edge_config.id_ if edge_config.id_ is not MISSING else None
            ),
        )
        self._resolvers.set_context(resolver_context)

        await self._ensure_segment_resolver(resolver_context)
        await self._ensure_application_resolver(resolver_context)

        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=resolver_context,
        )
        state.take_snapshot()

        if validate:
            read_result = self._validators.run(state, phase="read")
            for err in read_result.errors:
                state.load_warnings.append(_validation_error_to_warning(err))
            for warn in read_result.warnings:
                state.load_warnings.append(_validation_error_to_warning(warn))

        return state

    async def load_profile(
        self,
        profile: int | str,
        *,
        enterprise_id: int | None = None,
    ) -> ConfigState:
        """Load a profile configuration directly (no edge/template merge)."""
        profile_id: int

        if isinstance(profile, str):
            # Resolve profile name to ID
            ctx = ResolverContext(enterprise_id=enterprise_id)
            self._resolvers.set_context(ctx)
            profile_resolver = self._resolvers.get_resolver(ProfileResolver)
            await profile_resolver.load(ctx)

            identity = await self._resolvers.resolve(ProfileName(profile))
            profile_id = identity.id
        else:
            profile_id = profile

        # Fetch the profile configuration with modules
        request = GetConfiguration(
            id_=profile_id,
            with_=[GetConfigurationWith.MODULES],
        )
        if enterprise_id is not None:
            request.enterprise_id = enterprise_id

        config = await self.client.get_configuration(request)

        # Wrap it as a profile
        if not isinstance(config, EnterpriseConfigurationProfile):
            profile_config = EnterpriseConfigurationProfile.model_validate(
                config.model_dump()
            )
        else:
            profile_config = config

        resolver_context = ResolverContext(
            enterprise_id=enterprise_id,
            configuration_id=(
                profile_config.id_ if profile_config.id_ is not MISSING else None
            ),
        )
        self._resolvers.set_context(resolver_context)

        await self._ensure_segment_resolver(resolver_context)
        await self._ensure_application_resolver(resolver_context)

        state = ConfigState(
            edge_config=None,
            profile_config=profile_config,
            target_type="profile",
            resolver_context=resolver_context,
        )
        state.take_snapshot()

        return state

    async def apply(
        self,
        state: ConfigState,
        *,
        module: str,
        patch: dict[str, object],
    ) -> ConfigState:
        """Apply a patch dict to a module.

        1. Locate (or create from the profile template) the module on the
           target config.
        2. Call ``merge_patch()`` on the module's typed data.
        3. Return the updated :class:`ConfigState`.

        MISSING fields on the patch target are preserved as MISSING so callers
        downstream (reconciler, YAML export) can distinguish "unset" from
        "explicitly set". Use ``apply_aspect`` if you need template-fill
        semantics.
        """
        internal_name = _resolve_module_name(module)

        # Get the target config
        if state.target_type == "edge" and state.edge_config is not None:
            target_config = state.edge_config
        else:
            target_config = state.profile_config

        # Find or create the module on the target config
        mod = self._get_or_create_module(target_config, internal_name, state)

        # Apply merge_patch to the module's data
        if not isinstance(mod.data, VcoModel):
            raise TypeError(
                f"Module '{internal_name}' has unstructured data; merge_patch requires a typed module"
            )
        _ = mod.data.merge_patch(patch, resolvers=self._resolvers)

        return state

    async def save(
        self,
        state: ConfigState,
        *,
        dry_run: bool = False,
        validate: bool = True,
    ) -> SaveResult:
        """Persist changes to VCO.

        1. Diff current state against snapshot.
        2. Run write-time validators. Errors block save.
        3. Determine insert vs update per module.
        4. Send API calls.
        """
        changed_modules = state.get_changed_modules()
        result = SaveResult()

        if not changed_modules:
            return result

        # Run write-time validators
        if validate:
            validation = self._validators.run(
                state, phase="write", modules=changed_modules
            )
            if not validation.is_valid:
                result.validation_errors = validation.errors
                return result
            for warn in validation.warnings:
                result.warnings.append(_validation_error_to_warning(warn))

        if dry_run:
            result.modules_updated = changed_modules
            return result

        # Get the target config
        if state.target_type == "edge" and state.edge_config is not None:
            target_config = state.edge_config
        else:
            target_config = state.profile_config

        snapshot_config = state.snapshot_for_target()

        for module_name in changed_modules:
            mod = target_config.get_module(module_name)

            # Determine if this is an insert or update
            is_insert = snapshot_config is not None and not snapshot_config.has_module(
                module_name
            )

            # Set config context so EdgeOnly/ProfileOnly fields serialize correctly
            if isinstance(mod.data, VcoModel):
                if state.target_type == "profile":
                    _ = mod.data.for_profile()
                else:
                    _ = mod.data.for_edge()

            if is_insert:
                await self._insert_module(
                    mod, target_config, state.resolver_context.enterprise_id
                )
                result.modules_inserted.append(module_name)
            else:
                await self._update_module(mod, state.resolver_context.enterprise_id)
                result.modules_updated.append(module_name)

        return result

    def validate(self, state: ConfigState) -> ValidationResult:
        """Run all write-time validators without saving (dry-run validation)."""
        return self._validators.run(state, phase="write")

    # ------------------------------------------------------------------
    # Aspect operations
    # ------------------------------------------------------------------

    def read_aspect(
        self,
        state: ConfigState,
        name: str,
        segment: str | None = None,
    ) -> object:
        """Extract the subtree for a named aspect, serialized compactly.

        For segment-scoped aspects, the segment parameter is required and
        specifies which segment to read from.
        """
        aspect = self._aspect_registry.get(name)
        data = cast(object, self._get_aspect_data(state, aspect, segment))
        if isinstance(data, VcoModel):
            return data.model_dump(by_alias=False, context={"compact": True})
        if isinstance(data, list):
            items = cast(list[object], data)
            return [
                (
                    item.model_dump(by_alias=False, context={"compact": True})
                    if isinstance(item, VcoModel)
                    else item
                )
                for item in items
            ]
        return data

    async def apply_aspect(
        self,
        state: ConfigState,
        name: str,
        patch: dict[str, Any],
        segment: str | None = None,
    ) -> ConfigState:
        """Apply a patch dict to the subtree of a named aspect.

        For segment-scoped aspects, the segment parameter is required.
        Navigates to the parent model and applies the patch at the
        aspect's final path component.
        """
        aspect = self._aspect_registry.get(name)
        parent, field_name = self._get_aspect_parent(state, aspect, segment)
        _ = parent.merge_patch({field_name: patch}, resolvers=self._resolvers)
        return state

    async def apply_aspects(
        self,
        state: ConfigState,
        aspects: dict[str, dict[str, Any]],
    ) -> ConfigState:
        """Apply patches to multiple aspects sequentially.

        Patches are applied in mapping iteration order. If a later patch fails,
        changes from earlier patches remain in ``state``. This method does not
        save the state or run the pipeline validators.
        The dict maps aspect names to patch dicts. For segment-scoped aspects,
        use the format {"aspect_name": {"segment_name": patch_dict}}.
        """
        for name, patch in aspects.items():
            aspect = self._aspect_registry.get(name)
            if aspect.segment_scoped:
                # For segment-scoped: patch is {segment_name: segment_patch}
                for seg_name, seg_patch in patch.items():
                    _ = await self.apply_aspect(
                        state, name, seg_patch, segment=seg_name
                    )
            else:
                _ = await self.apply_aspect(state, name, patch)
        return state

    def list_aspects(self) -> list[AspectSummary]:
        """List all registered aspects."""
        return self._aspect_registry.list_aspects()

    def search_aspects(self, query: str) -> list[AspectSummary]:
        """Search aspects by keyword."""
        return self._aspect_registry.search(query)

    def inspect_aspect(
        self,
        name: str,
        module_models: dict[ConfigurationModuleName, type[VcoModel]] | None = None,
    ) -> dict[str, Any]:
        """Return the aspect definition and its patch schema."""
        aspect = self._aspect_registry.get(name)
        result: dict[str, Any] = {
            "name": aspect.name,
            "module": aspect.module,
            "path": aspect.path,
            "description": aspect.description,
            "tags": aspect.tags,
            "segment_scoped": aspect.segment_scoped,
            "examples": aspect.examples,
        }
        if module_models:
            result["patch_schema"] = self._aspect_registry.get_patch_schema(
                name, module_models=module_models
            )
        return result

    def _navigate_to_base(
        self,
        state: ConfigState,
        aspect: Aspect,
        segment: str | None,
    ) -> Any:
        return navigate_to_base(state, aspect, segment)

    def _get_aspect_data(
        self,
        state: ConfigState,
        aspect: Aspect,
        segment: str | None,
    ) -> Any:
        return get_aspect_data(state, aspect, segment)

    def _get_aspect_parent(
        self,
        state: ConfigState,
        aspect: Aspect,
        segment: str | None,
    ) -> tuple[VcoModel, str]:
        return get_aspect_parent(state, aspect, segment)

    async def load_resolvers(self, context: ResolverContext) -> None:
        """Ensure segment and application resolvers are loaded for the given context.

        Called automatically by load() and load_profile(). Call explicitly when
        bypassing those methods, e.g. when loading config state from a cache.
        """
        await self._ensure_segment_resolver(context)
        await self._ensure_application_resolver(context)

    async def _ensure_segment_resolver(self, context: ResolverContext) -> None:
        """Ensure segment resolver is registered and loaded from VCO API."""
        try:
            resolver = self._resolvers.get_resolver(SegmentResolver)
        except LookupError:
            resolver = SegmentResolver(self.client)
            self._resolvers.register(resolver)
        await resolver.load(context)

    async def _ensure_application_resolver(self, context: ResolverContext) -> None:
        """Ensure application resolver is loaded if registered."""
        try:
            resolver = self._resolvers.get_resolver(ApplicationResolver)
            await resolver.load(context)
        except LookupError:
            pass

    def _get_or_create_module(
        self,
        config: ConfigurationBase,
        module_name: ConfigurationModuleName,
        state: ConfigState,
    ) -> AnyConfigurationModule:
        """Get existing module or prepare for creation."""
        if config.has_module(module_name):
            return config.get_module(module_name)

        # Module doesn't exist on the target - create it from the profile template
        if state.target_type == "edge" and state.profile_config.has_module(module_name):
            template_mod = state.profile_config.get_module(module_name)
            new_mod = copy.deepcopy(template_mod)
            if config.modules is MISSING:
                config.modules = []
            config.modules.append(new_mod)
            return new_mod

        raise ValueError(
            f"Module '{module_name}' not found on config and no template available"
        )

    async def _update_module(
        self,
        mod: AnyConfigurationModule,
        enterprise_id: int | None,
    ) -> None:
        """Send update_configuration_module API call."""
        if isinstance(mod, DeviceSettingsConfigurationModule):
            update = DeviceSettingsModuleUpdate(
                data=mod.data,
                refs=getattr(mod, "refs", MISSING),
            )
        elif isinstance(mod, QosConfigurationModule):
            update = QosModuleUpdate(
                data=mod.data,
                refs=getattr(mod, "refs", MISSING),
            )
        elif isinstance(mod, WanConfigurationModule):
            update = WanModuleUpdate(data=mod.data)
        elif isinstance(mod, FirewallConfigurationModule):
            update = FirewallModuleUpdate(data=mod.data)
        else:
            raise TypeError(f"Cannot update module type: {type(mod).__name__}")

        request = UpdateConfigurationModule(
            configuration_module_id=mod.id_,
            enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
            update=update,
        )
        await self.client.update_configuration_module(request)

    async def _insert_module(
        self,
        mod: AnyConfigurationModule,
        config: ConfigurationBase,
        enterprise_id: int | None,
    ) -> None:
        """Send insert_configuration_module API call."""
        config_id = config.id_
        if config_id is MISSING or config_id is None:
            raise ValueError("Cannot insert module: configuration has no ID")

        if isinstance(mod, FirewallConfigurationModule):
            request = InsertFirewallConfigurationModule(
                configuration_id=config_id,
                data=mod.data,
                enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
            )
        elif isinstance(mod, QosConfigurationModule):
            refs = mod.refs if mod.refs is not MISSING else None
            request = InsertQosConfigurationModule(
                configuration_id=config_id,
                data=mod.data,
                refs=refs,
                enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
            )
        else:
            raise TypeError(f"Cannot insert module type: {type(mod).__name__}")

        _ = await self.client.insert_configuration_module(request)


def build_default_pipeline(client: VcoClientProtocol) -> ConfigPipeline:
    """Build a ConfigPipeline with default resolvers and aspects.

    This is the standard bootstrap for intent operations: registers
    EdgeResolver, ProfileResolver, and all default aspects.
    """
    resolvers = ResolverRegistry()
    resolvers.register(EdgeResolver(client))
    resolvers.register(ProfileResolver(client))

    aspects = AspectRegistry()
    register_default_aspects(aspects)

    return ConfigPipeline(client, resolvers=resolvers, aspect_registry=aspects)


def _validation_error_to_warning(err: ValidatorError) -> ValidationWarning:
    """Convert a ValidationError to a ValidationWarning for load-time use."""
    return ValidationWarning(
        path=err.path,
        message=err.message,
        code=err.code,
        severity=err.severity,
    )
