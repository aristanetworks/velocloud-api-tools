from __future__ import annotations

import copy
import datetime
import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self, TypeIs, overload

from pydantic import (
    MISSING,
    Discriminator,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    RootModel,
    ValidationInfo,
    model_validator,
)
from pydantic_core import PydanticSerializationError, to_jsonable_python

from pyvelo.client.enums.configuration import (
    ConfigurationBastionState,
    ConfigurationModuleName,
    ConfigurationModuleType,
    ConfigurationScope,
    ConfigurationType,
)
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    DeviceSettingsRefs,
    FirewallData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.control_plane.root import (
    ControlPlaneData,
)
from pyvelo.core import (
    Alias,
    ConfigContext,
    NoneIsMissing,
    Tinyint,
    VcoModel,
    VcoTimestamp,
)
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, ProfileIdentity


class ConfigurationBase(VcoModel):
    name: str
    configuration_type: ConfigurationType = ConfigurationType.SEGMENT_BASED
    bastion_state: ConfigurationBastionState | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    edge_count: int | None | MISSING = MISSING
    effective: VcoTimestamp | None | MISSING = MISSING
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    enterprise_logical_id: uuid.UUID | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    schema_version: str | None | MISSING = MISSING
    version: str | None | MISSING = MISSING
    is_staging: Tinyint | None | MISSING = MISSING
    modules: list[AnyConfigurationModule] | MISSING = MISSING

    def _get_module(
        self, module_name: ConfigurationModuleName
    ) -> AnyConfigurationModule:
        if self.modules is MISSING:
            raise ValueError("Configuration has no modules")
        for m in self.modules:
            if m.name == module_name:
                return m
        raise ValueError(f"Module '{module_name}' not found in configuration")

    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.DEVICE_SETTINGS]
    ) -> DeviceSettingsConfigurationModule: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.QOS]
    ) -> QosConfigurationModule: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.FIREWALL]
    ) -> FirewallConfigurationModule: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.WAN]
    ) -> WanConfigurationModule: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.CONTROL_PLANE]
    ) -> ControlPlaneConfigurationModule: ...
    @overload
    def get_module(self, name: ConfigurationModuleName) -> AnyConfigurationModule: ...

    def get_module(self, name: ConfigurationModuleName) -> AnyConfigurationModule:
        return self._get_module(name)

    def has_module(self, name: ConfigurationModuleName) -> bool:
        if self.modules is MISSING:
            return False
        return any(m.name == name for m in self.modules)

    @property
    def device_settings(self) -> DeviceSettingsConfigurationModule:
        m = self._get_module(ConfigurationModuleName.DEVICE_SETTINGS)
        assert isinstance(m, DeviceSettingsConfigurationModule)
        return m

    @property
    def qos(self) -> QosConfigurationModule:
        m = self._get_module(ConfigurationModuleName.QOS)
        assert isinstance(m, QosConfigurationModule)
        return m

    @property
    def firewall(self) -> FirewallConfigurationModule:
        m = self._get_module(ConfigurationModuleName.FIREWALL)
        assert isinstance(m, FirewallConfigurationModule)
        return m

    @property
    def wan(self) -> WanConfigurationModule:
        m = self._get_module(ConfigurationModuleName.WAN)
        assert isinstance(m, WanConfigurationModule)
        return m

    @property
    def control_plane(self) -> ControlPlaneConfigurationModule:
        m = self._get_module(ConfigurationModuleName.CONTROL_PLANE)
        assert isinstance(m, ControlPlaneConfigurationModule)
        return m


class EdgeConfiguration(ConfigurationBase):
    _config_context: ConfigContext | None = PrivateAttr(default=ConfigContext.EDGE)


class EnterpriseConfigurationProfile(ConfigurationBase):
    _config_context: ConfigContext | None = PrivateAttr(default=ConfigContext.PROFILE)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _capture(
    source: Mapping[object, object], key: str, mode: Literal["python", "json"]
) -> object:
    """Snapshot ``source[key]`` as pure JSON, before validation touches it.

    ``MISSING`` if the key is absent. JSON-mode input is already pure JSON and
    is deep-copied. Python-mode input may hold models at any depth, so it is
    converted with ``to_jsonable_python`` (models dumped by alias). Either way
    the snapshot shares no containers with the caller's objects, so later
    edits to those cannot change what was captured.
    """
    if key not in source:
        return MISSING
    value = source[key]
    if mode == "json":
        return copy.deepcopy(value)
    return to_jsonable_python(value, by_alias=True)


class ConfigurationModuleBase(VcoModel):
    created: VcoTimestamp
    modified: VcoTimestamp
    id_: Annotated[int, Alias("id")]
    type_: Annotated[ConfigurationModuleType, Alias("type")]
    description: str | None
    configuration_id: Annotated[int | None, Resolvable(ProfileIdentity, "id")]
    enterprise_logical_id: uuid.UUID | None
    schema_version: str | None
    version: str | None
    metadata: dict[str, Any] | None

    _raw_data: object = PrivateAttr(default=MISSING)
    _raw_refs: object = PrivateAttr(default=MISSING)
    _raw_mode: Literal["python", "json"] = PrivateAttr(default="python")
    _is_new: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    @classmethod
    def _capture_raw(
        cls,
        value: object,
        handler: ModelWrapValidatorHandler[Self],
        info: ValidationInfo,
    ) -> Self:
        if not _is_mapping(value):
            # An existing instance keeps the snapshot it already has.
            return handler(value)
        try:
            raw_data = _capture(value, "data", info.mode)
            raw_refs = _capture(value, "refs", info.mode)
        except PydanticSerializationError:
            # Input that cannot become JSON is usually input that does not
            # validate: let validation report that before anything else.
            _ = handler(value)
            raise
        module = handler(value)
        module._raw_data = raw_data
        module._raw_refs = raw_refs
        module._raw_mode = info.mode
        return module

    @property
    def raw_data(self) -> object:
        """``data`` exactly as validated; ``MISSING`` if the input had none."""
        return self._raw_data

    @property
    def raw_refs(self) -> object:
        """``refs`` exactly as validated; ``MISSING`` if the input had none."""
        return self._raw_refs

    @property
    def raw_mode(self) -> Literal["python", "json"]:
        """How the module was first validated."""
        return self._raw_mode

    @property
    def is_new(self) -> bool:
        """True only for modules made by :func:`new_edge_module`."""
        return self._is_new


class ControlPlaneConfigurationModule(ConfigurationModuleBase):
    name: Literal["controlPlane"] = "controlPlane"
    data: ControlPlaneData

    def mutable_data(self) -> ControlPlaneData:
        return copy.deepcopy(self.data)


class DeviceSettingsConfigurationModule(ConfigurationModuleBase):
    name: Literal["deviceSettings"] = "deviceSettings"
    data: DeviceSettingsData
    refs: DeviceSettingsRefs | MISSING = MISSING

    def mutable_data(self) -> DeviceSettingsData:
        return copy.deepcopy(self.data)


class WanConfigurationModule(ConfigurationModuleBase):
    name: Literal["WAN"] = "WAN"
    data: WanData

    def mutable_data(self) -> WanData:
        return copy.deepcopy(self.data)


class QosConfigurationModule(ConfigurationModuleBase):
    name: Literal["QOS"] = "QOS"
    data: QosData
    refs: dict[str, Any] | MISSING = MISSING

    def mutable_data(self) -> QosData:
        return copy.deepcopy(self.data)


class FirewallConfigurationModule(ConfigurationModuleBase):
    name: Literal["firewall"] = "firewall"
    data: FirewallData

    def mutable_data(self) -> FirewallData:
        return copy.deepcopy(self.data)


class GenericConfigurationModule(ConfigurationModuleBase):
    name: Literal["analyticsSettings", "atpMetadata"]
    data: dict[str, Any]


type AnyConfigurationModule = Annotated[
    ControlPlaneConfigurationModule
    | DeviceSettingsConfigurationModule
    | WanConfigurationModule
    | QosConfigurationModule
    | FirewallConfigurationModule
    | GenericConfigurationModule,
    Discriminator(discriminator="name"),
]

ConfigurationModuleList = RootModel[list[AnyConfigurationModule]]

EMPTY_EDGE_MODULE_DATA: dict[ConfigurationModuleName, dict[str, Any]] = {
    ConfigurationModuleName.FIREWALL: {"segments": []},
    ConfigurationModuleName.QOS: {"segments": []},
}


def new_edge_module(
    name: ConfigurationModuleName, configuration_id: int
) -> FirewallConfigurationModule | QosConfigurationModule:
    """Build an empty edge-layer module that does not exist on the VCO yet."""
    if name not in EMPTY_EDGE_MODULE_DATA:
        raise ValueError(
            f"Module {name.value!r} cannot be inserted as an empty edge layer"
        )
    now = datetime.datetime.now(datetime.UTC)
    row: dict[str, Any] = {
        "id": 0,
        "type": "ENTERPRISE",
        "created": now,
        "modified": now,
        "description": None,
        "configurationId": configuration_id,
        "enterpriseLogicalId": None,
        "schemaVersion": None,
        "version": None,
        "metadata": None,
        "name": name.value,
        "data": copy.deepcopy(EMPTY_EDGE_MODULE_DATA[name]),
    }
    module = (
        FirewallConfigurationModule
        if name is ConfigurationModuleName.FIREWALL
        else QosConfigurationModule
    ).model_validate(row)
    module._is_new = True  # pyright: ignore[reportPrivateUsage]
    return module


# Rebuild models that forward-reference AnyConfigurationModule.  These classes
# are defined above before AnyConfigurationModule exists; Pydantic cannot
# resolve the forward reference at class-creation time, leaving a MockValSer
# placeholder instead of a real SchemaSerializer.  Rebuilding here (after the
# type alias exists) replaces the placeholder so that both model_dump_json()
# *and* pydantic_core.to_json() work correctly.
ConfigurationBase.model_rebuild(force=True)
EdgeConfiguration.model_rebuild(force=True)
EnterpriseConfigurationProfile.model_rebuild(force=True)


class OperatorConfigurationModule(VcoModel):
    is_edge_specific: Tinyint | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    type_: Annotated[ConfigurationModuleType | None | MISSING, Alias("type")] = MISSING
    version: str | None | MISSING = MISSING


class OperatorConfiguration(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    modules: list[OperatorConfigurationModule] | None | MISSING = MISSING
    name: str | None | MISSING = MISSING


class EnterpriseConfigurationModule(VcoModel):
    edge_specific_data: dict[str, Any] | None | MISSING = MISSING
    is_edge_specific: Tinyint | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    type_: Annotated[ConfigurationModuleType | None | MISSING, Alias("type")] = MISSING
    version: str | None | MISSING = MISSING


class EnterpriseConfiguration(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    modules: list[EnterpriseConfigurationModule] | None | MISSING = MISSING
    name: str | None | MISSING = MISSING


class Configuration(VcoModel):
    operator: OperatorConfiguration | None | MISSING = MISSING
    enterprise: EnterpriseConfiguration | None | MISSING = MISSING


class DeviceSettingsModuleUpdate(VcoModel):
    data: DeviceSettingsData
    refs: DeviceSettingsRefs | MISSING = MISSING


class WanModuleUpdate(VcoModel):
    data: WanData


class QosModuleUpdate(VcoModel):
    data: QosData
    refs: dict[str, Any] | MISSING = MISSING


class FirewallModuleUpdate(VcoModel):
    data: FirewallData


ConfigurationModuleUpdate = (
    DeviceSettingsModuleUpdate
    | WanModuleUpdate
    | QosModuleUpdate
    | FirewallModuleUpdate
)


class UpdateConfigurationModule(VcoModel):
    configuration_module_id: Annotated[int, Alias("id")]
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    # Typed as Any to avoid Pydantic serialization warnings for the undiscriminated
    # ConfigurationModuleUpdate union; callers always pass a concrete subtype.
    update: Annotated[Any, Alias("_update")]


class InsertConfigurationModuleResult(VcoModel):
    id_: Annotated[int, Alias("id")]
    rows: int | None | MISSING = MISSING
    error: str | None | MISSING = MISSING


class InsertConfigurationModuleBase(VcoModel):
    description: str | None = ""
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    type_: Annotated[ConfigurationScope, Alias("type")] = ConfigurationScope.ENTERPRISE


class InsertQosConfigurationModule(InsertConfigurationModuleBase):
    name: Literal["QOS"] = "QOS"
    data: QosData
    refs: dict[str, Any] | None = None


class InsertFirewallConfigurationModule(InsertConfigurationModuleBase):
    name: Literal["firewall"] = "firewall"
    data: FirewallData


type InsertConfigurationModule = Annotated[
    InsertQosConfigurationModule | InsertFirewallConfigurationModule,
    Discriminator(discriminator="name"),
]


class GetConfigurationModules(VcoModel):
    enterprise_id: int | MISSING = MISSING
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    no_data: bool | MISSING = MISSING
    modules: list[ConfigurationModuleName] | MISSING = MISSING


class GetConfiguration(VcoModel):
    id_: Annotated[int, Alias("id"), Resolvable(ProfileIdentity, "id")] = Field(
        description="Configuration ID"
    )
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[GetConfigurationWith] | MISSING, Alias("with")] = MISSING


class GetIdentifiableApplications(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    application_ids: list[int] | MISSING = MISSING


class GetRoutableApplications(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING


class KnownIpPortMapping(VcoModel):
    subnets: list[str] | MISSING = MISSING
    tcp_ports: list[int | tuple[int, int]] | MISSING = MISSING
    udp_ports: list[int | tuple[int, int]] | MISSING = MISSING


class FqdnMapping(VcoModel):
    fqdn_list: Annotated[list[str] | MISSING, Alias("FQDNList")] = MISSING


class ProtocolPortMapping(VcoModel):
    """Port mapping for an application's protocol."""

    tcp_ports: list[int | tuple[int, int]] | MISSING = MISSING
    udp_ports: list[int | tuple[int, int]] | MISSING = MISSING


class ApplicationItem(VcoModel):
    """A VeloCloud application definition used for DPI classification."""

    id_: Annotated[int, Alias("id")]
    name: str | MISSING = MISSING
    display_name: str
    class_: Annotated[int, Alias("class")]
    description: str
    known_ip_port_mapping: KnownIpPortMapping
    fqdn_mapping: Annotated[FqdnMapping | MISSING, Alias("FQDNMapping")] = MISSING
    protocol_port_mapping: ProtocolPortMapping
    do_not_slow_learn: Tinyint | MISSING = MISSING
    must_not_perform_dpi: Tinyint | MISSING = MISSING


class GetApplicationsResult(VcoModel):
    applications: list[ApplicationItem]
    application_classes: list[str]
    application_class_to_application: dict[str, list[int]]
    application_to_application_class: dict[str, int]
    custom_app_ids: list[int] | MISSING = MISSING
    metadata: Annotated[dict[str, Any] | MISSING, Alias("metaData")] = MISSING


class GetAllApplicationsResult(VcoModel):
    all: GetApplicationsResult
    routable: GetApplicationsResult


class GetAnalyticsConfiguration(VcoModel):
    enterprise_id: int | MISSING = MISSING


class GetAnalyticsConfigurationResult(VcoModel):
    analytics: dict[str, Any] | None | MISSING = MISSING


class DeleteConfiguration(VcoModel):
    """Request to delete a configuration/profile."""

    id_: Annotated[int, Alias("id"), Resolvable(ProfileIdentity, "id")]
    enterprise_id: int | MISSING = MISSING


__all__ = [
    "EMPTY_EDGE_MODULE_DATA",
    "AnyConfigurationModule",
    "Configuration",
    "ConfigurationBase",
    "ConfigurationModuleBase",
    "ConfigurationModuleUpdate",
    "ControlPlaneConfigurationModule",
    "DeleteConfiguration",
    "DeviceSettingsConfigurationModule",
    "DeviceSettingsModuleUpdate",
    "EdgeConfiguration",
    "EnterpriseConfigurationModule",
    "EnterpriseConfigurationProfile",
    "FirewallConfigurationModule",
    "FirewallModuleUpdate",
    "GetAnalyticsConfiguration",
    "GetAnalyticsConfigurationResult",
    "GetApplicationsResult",
    "GetConfiguration",
    "GetConfigurationModules",
    "GetIdentifiableApplications",
    "GetRoutableApplications",
    "InsertConfigurationModule",
    "InsertConfigurationModuleResult",
    "OperatorConfiguration",
    "OperatorConfigurationModule",
    "QosConfigurationModule",
    "QosModuleUpdate",
    "UpdateConfigurationModule",
    "WanConfigurationModule",
    "WanModuleUpdate",
    "new_edge_module",
]
