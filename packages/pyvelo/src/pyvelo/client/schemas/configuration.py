"""A configuration and its module rows, and the ``configuration/`` method models.

A profile's or an Edge's configuration holds module rows (``deviceSettings``, ``WAN``,
``QOS``, ``firewall``, ``controlPlane`` and more), each typed by its layer ``L``. A row
keeps the JSON it was read from (``capture_key``), so a module write starts from the
raw data. Also the requests and results of the module reads, inserts and updates, the
application lists, and configuration delete.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import (
    Annotated,
    Any,
    ClassVar,
    Generic,
    Literal,
    Self,
    TypeIs,
    cast,
    get_args,
    overload,
    override,
)

from pydantic import (
    MISSING,
    ConfigDict,
    Discriminator,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    RootModel,
    Tag,
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
from pyvelo.client.models.configuration.common import segment_id_of
from pyvelo.client.models.configuration.control_plane.root import (
    ControlPlaneData,
)
from pyvelo.client.models.configuration.profile_segments import (
    AddedDefaults,
    AddedSegment,
    SegmentAddition,
    insert_defaults,
    with_additions,
)
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.models.configuration.ref_owners import fill_ref_only
from pyvelo.core import (
    Alias,
    ConfigContext,
    NoneIsMissing,
    RowsModifiedConfirmation,
    Tinyint,
    VcoModel,
    VcoTimestamp,
)
from pyvelo.core.base import field_models
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, ProfileIdentity
from pyvelo.core.layers import Composed, Edge, Effective, L, Profile


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def capture_key(
    source: Mapping[object, object], key: str, mode: Literal["python", "json"]
) -> object:
    """Snapshot ``source[key]`` as pure JSON, before validation touches it.

    ``MISSING`` if the key is absent or explicitly ``MISSING`` (``refs`` is
    declared ``... | MISSING``, and ``dict(module)`` carries it). JSON-mode
    input is already pure JSON and is deep-copied. Python-mode input may hold
    models at any depth, so it is converted with ``to_jsonable_python`` (models
    dumped by alias). Either way the snapshot shares no containers with the
    caller's objects, so later edits to those cannot change what was captured.
    """
    value = source.get(key, MISSING)
    if value is MISSING:
        return MISSING
    if mode == "json":
        return copy.deepcopy(value)
    return to_jsonable_python(value, by_alias=True)


class ConfigurationModuleBase(VcoModel):
    created: VcoTimestamp
    modified: VcoTimestamp
    id_: Annotated[int, Alias("id")]
    type_: Annotated[ConfigurationModuleType, Alias("type")]
    description: str | None
    configuration_id: Annotated[int | None, Resolvable(ProfileIdentity, lambda i: i.id)]
    enterprise_logical_id: uuid.UUID | None
    schema_version: str | None
    version: str | None
    metadata: dict[str, Any] | None

    _raw_data: object = PrivateAttr(default=MISSING)
    _raw_refs: object = PrivateAttr(default=MISSING)
    _raw_mode: Literal["python", "json"] = PrivateAttr(default="python")
    _is_new: bool = PrivateAttr(default=False)
    _refs_loaded: bool = PrivateAttr(default=True)
    _additions: tuple[SegmentAddition, ...] = PrivateAttr(default=())
    _repair_ids: dict[tuple[str, str], str] = PrivateAttr(default_factory=dict)

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
            raw_data = capture_key(value, "data", info.mode)
            raw_refs = capture_key(value, "refs", info.mode)
        except PydanticSerializationError:
            # Input that cannot become JSON is usually input that does not
            # validate. Let validation report that field-level error first; if
            # it passes, re-raise (pydantic surfaces it as a model-level
            # ``value_error``, since PydanticSerializationError is a ValueError).
            _ = handler(value)
            raise
        module = handler(value)
        module._raw_data = raw_data
        module._raw_refs = raw_refs
        module._raw_mode = info.mode
        context: object = info.context
        loaded = context.get("refs_loaded", True) if _is_mapping(context) else True
        module._refs_loaded = loaded is not False
        data: object = getattr(module, "data", None)
        if module._refs_loaded and isinstance(data, VcoModel):
            fill_ref_only(data, raw_refs)
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

    @property
    def refs_loaded(self) -> bool:
        """Whether the read that produced this module promised its refs.

        A module from ``getConfigurationModules`` or an insert's ``returnData``
        is not loaded, and its ``RefOnly`` fields are ``MISSING``; any other
        is, and an absent ``refs`` key then means no rows. It is the
        validation context key ``"refs_loaded"``, ``True`` when absent.
        """
        return self._refs_loaded

    @property
    def additions(self) -> tuple[SegmentAddition, ...]:
        """The entries the pipeline added to this profile module, in the order added.

        A plan of the module is made against its raw data plus these
        (``plan_module``), so an added entry nobody edited plans no change.
        """
        return self._additions

    def _segments_to_add_to(self) -> tuple[VcoModel, list[Any]]:
        """The module's data and its ``segments`` list, for a profile module that has them.

        Raises ``ValueError`` for a module that is not at the profile level or
        whose data has no ``segments`` list.
        """
        if self.config_context is not ConfigContext.PROFILE:
            raise ValueError(
                f"Module {self.id_} is not at the profile level: a segment is "
                "added to the modules of a profile only."
            )
        data: object = getattr(self, "data", MISSING)
        segments: object = getattr(data, "segments", MISSING)
        if isinstance(data, VcoModel) and isinstance(segments, list):
            return data, cast("list[Any]", segments)
        raise ValueError(
            f"The data of module {self.id_} has no `segments`, so it has no "
            "segment to add to."
        )

    def _parse_as(self, model_type: type[VcoModel], raw: object) -> Any:
        """*raw* parsed as *model_type* the way this module was first parsed, at its config level."""
        context = {"config_level": self.config_context}
        if self._raw_mode == "json":
            return model_type.model_validate_json(json.dumps(raw), context=context)
        return model_type.model_validate(raw, context=context)

    def add_segment_entry(self, entry: Mapping[str, object]) -> VcoModel:
        """Add the segment *entry* to this profile module, and return its item of ``data.segments``.

        *entry* is the raw entry, as ``new_segment_entry`` makes it. It is
        recorded as an ``AddedSegment`` (a copy of it), which makes it part of
        the baseline the module is planned against: written as it is, it is
        no change. The item is *entry* parsed as the data's ``segments`` item
        class at this module's config level, and appended to ``data.segments``.

        Raises ``ValueError`` when the module is not at the profile level, has
        no ``segments``, or its data already holds the entry's ``segmentId``.
        """
        data, segments = self._segments_to_add_to()
        (item_type,) = field_models(type(data).model_fields["segments"])
        added = copy.deepcopy(dict(entry))
        item = self._parse_as(item_type, added)
        segment_id: int = item.segment.segment_id
        name: str = item.segment.name
        if segment_id_of(added) != segment_id:
            raise ValueError(
                f"The entry for segment {name!r} has no `segmentId`: give its "
                "`segment` descriptor the segment's ID."
            )
        if any(held.segment.segment_id == segment_id for held in segments):
            raise ValueError(
                f"Segment {name!r} (segmentId {segment_id}) is already in the "
                f"data of module {self.id_}."
            )
        segments.append(item)
        self._additions = (*self._additions, AddedSegment(segment_id, name, added))
        return item

    def add_default_entries(
        self, segment_id: int, entries: Sequence[Mapping[str, object]]
    ) -> None:
        """Insert the raw QoS rules *entries* into the ``defaults`` of segment *segment_id*.

        They are recorded as an ``AddedDefaults``, part of the baseline the
        module is planned against, and inserted, parsed as ``QosRule``, into
        the segment's ``defaults`` (an unset one becomes the list) at the
        place ``with_additions`` gives them in the raw data. QoS modules only.

        Raises ``TypeError`` for another module, and ``ValueError`` when the
        module is not at the profile level or its data has no such segment.
        """
        if getattr(self, "name", None) != "QOS":
            raise TypeError(
                "Default entries are a QoS segment's: this is not a QoS module "
                f"(it is module {self.id_})."
            )
        _, segments = self._segments_to_add_to()
        found = [s for s in segments if s.segment.segment_id == segment_id]
        if not found:
            raise ValueError(
                f"The data of module {self.id_} has no segment with segmentId "
                f"{segment_id} to add default entries to."
            )
        segment = found[0]
        added = tuple(copy.deepcopy(dict(entry)) for entry in entries)
        rules = [self._parse_as(QosRule, entry) for entry in added]
        defaults: object = segment.defaults
        held: list[Any] = list(defaults) if isinstance(defaults, list) else []
        # The place is decided on the raw items (a parsed rule has normalized
        # its match), which stand in the same order as the parsed ones.
        raw_held = self._raw_defaults_of(segment_id)
        if len(raw_held) == len(held):
            placed = insert_defaults(raw_held, added)
            parsed = {id(raw): item for raw, item in zip(raw_held, held, strict=True)}
            parsed |= {id(raw): rule for raw, rule in zip(added, rules, strict=True)}
            segment.defaults = [parsed[id(item)] for item in placed]
        else:
            segment.defaults = [*held, *rules]
        self._additions = (
            *self._additions,
            AddedDefaults(segment_id, segment.segment.name, added),
        )

    def _raw_defaults_of(self, segment_id: int) -> list[object]:
        """The raw ``defaults`` of segment *segment_id* in the raw data plus the additions so far; empty when it has none."""
        if self._raw_data is MISSING:
            return []
        raw = with_additions(self._raw_data, self._additions)
        entries: object = raw.get("segments") if isinstance(raw, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and segment_id_of(entry) == segment_id:
                found: object = entry.get("defaults")
                return list(found) if isinstance(found, list) else []
        return []

    def defaults_rule_id(self, segment_id: object, name: str) -> str:
        """The ``ruleLogicalId`` kept for the repaired default *name* of segment *segment_id*.

        A new ``uuid4`` text the first time, the same every time after, so a
        plan, a re-plan and a plan of a copy of this module send one ID. A
        copy made by ``with_data_parsed_from`` starts with none.
        """
        key = (str(segment_id), name)
        if key not in self._repair_ids:
            self._repair_ids[key] = str(uuid.uuid4())
        return self._repair_ids[key]

    def with_data_parsed_from(
        self, raw_data: object, *, additions: Sequence[SegmentAddition] = ()
    ) -> Self:
        """A copy of this module whose ``data`` is *raw_data* parsed, and whose raw snapshot is this module's.

        The data is parsed at this module's config level, and its ``RefOnly``
        fields are filled from the refs of the snapshot, as a read does.
        ``plan_module`` of the copy is then the change from this module's raw
        data, plus *additions*, to *raw_data*. ``version``, the raw refs and
        ``refs_loaded`` are this module's. The copy's additions are
        *additions*, never this module's, and it keeps its own repair IDs
        (``defaults_rule_id``), none to begin with.
        """
        copied = self.model_copy()
        copied._additions = tuple(additions)
        copied._repair_ids = {}
        data: object = getattr(self, "data", MISSING)
        if isinstance(data, VcoModel):
            level = self.config_context
            parsed = type(data).model_validate_json(
                json.dumps(raw_data),
                context={"config_level": level} if level is not None else None,
            )
            if self._refs_loaded:
                fill_ref_only(parsed, self._raw_refs)
            object.__setattr__(copied, "data", parsed)
        elif data is not MISSING:
            object.__setattr__(copied, "data", copy.deepcopy(raw_data))
        return copied


class ControlPlaneConfigurationModule(ConfigurationModuleBase):
    name: Literal["controlPlane"] = "controlPlane"
    data: ControlPlaneData

    def mutable_data(self) -> ControlPlaneData:
        return copy.deepcopy(self.data)


class DeviceSettingsConfigurationModule(ConfigurationModuleBase):
    name: Literal["deviceSettings"] = "deviceSettings"
    data: DeviceSettingsData
    refs: DeviceSettingsRefs | MISSING = Field(default=MISSING, frozen=True)

    def mutable_data(self) -> DeviceSettingsData:
        return copy.deepcopy(self.data)


class WanConfigurationModule(ConfigurationModuleBase):
    name: Literal["WAN"] = "WAN"
    data: WanData

    def mutable_data(self) -> WanData:
        return copy.deepcopy(self.data)


class QosConfigurationModule(ConfigurationModuleBase, Generic[L]):  # noqa: UP046 (one shared constrained L, as a TypeVar)
    name: Literal["QOS"] = "QOS"
    data: QosData[L]
    refs: dict[str, Any] | MISSING = Field(default=MISSING, frozen=True)

    @property
    @override
    def config_context(self) -> ConfigContext | None:
        """The level of the layer this module is for: ``EDGE`` or ``PROFILE``, none for the others."""
        return self.data.config_context

    def mutable_data(self) -> QosData[L]:
        return copy.deepcopy(self.data)


class FirewallConfigurationModule(ConfigurationModuleBase, Generic[L]):  # noqa: UP046 (one shared constrained L, as a TypeVar)
    name: Literal["firewall"] = "firewall"
    data: FirewallData[L]

    @property
    @override
    def config_context(self) -> ConfigContext | None:
        """The level of the layer this module is for: ``EDGE`` or ``PROFILE``, none for the others."""
        return self.data.config_context

    def mutable_data(self) -> FirewallData[L]:
        return copy.deepcopy(self.data)


class GenericConfigurationModule(ConfigurationModuleBase):
    name: Literal["analyticsSettings", "atpMetadata"]
    data: dict[str, Any]


type AnyConfigurationModule[X: (Edge, Profile, Effective, Composed)] = Annotated[
    ControlPlaneConfigurationModule
    | DeviceSettingsConfigurationModule
    | WanConfigurationModule
    | QosConfigurationModule[X]
    | FirewallConfigurationModule[X]
    | GenericConfigurationModule,
    Discriminator(discriminator="name"),
]
"""A module row of the layer ``X``.

Only the QoS and firewall module classes depend on the layer so far; each of
the others joins when its data is converted.
"""

type AnyStoredModule = AnyConfigurationModule[Edge] | AnyConfigurationModule[Profile]
"""A module row of either stored layer, for code that handles both."""


class ConfigurationModuleList(RootModel[list[AnyConfigurationModule[L]]], Generic[L]):  # noqa: UP046 (one shared constrained L, as a TypeVar)
    """A list of module rows of one layer; a row that does not parse is refused without quoting it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(hide_input_in_errors=True)


def _wrong_module(module: object, expected: type) -> TypeError:
    """The error for a module of a configuration's ``modules`` whose class is not the one its name has."""
    return TypeError(
        f"the {getattr(module, 'name', '?')} module is a "
        f"{type(module).__name__}, not a {expected.__name__}"
    )


# The configurations come after ``AnyConfigurationModule`` on purpose: a
# parametrized base such as ``ConfigurationBase[Edge]`` takes its module list
# from the alias when it is made, and one made before the alias exists would
# keep it unresolved, with the layer still a type variable.
class ConfigurationBase(VcoModel, Generic[L]):  # noqa: UP046 (one shared constrained L, as a TypeVar)
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
    modules: list[AnyConfigurationModule[L]] | MISSING = MISSING

    def _get_module(
        self, module_name: ConfigurationModuleName
    ) -> AnyConfigurationModule[L]:
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
    ) -> QosConfigurationModule[L]: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.FIREWALL]
    ) -> FirewallConfigurationModule[L]: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.WAN]
    ) -> WanConfigurationModule: ...
    @overload
    def get_module(
        self, name: Literal[ConfigurationModuleName.CONTROL_PLANE]
    ) -> ControlPlaneConfigurationModule: ...
    @overload
    def get_module(
        self, name: ConfigurationModuleName
    ) -> AnyConfigurationModule[L]: ...

    def get_module(self, name: ConfigurationModuleName) -> AnyConfigurationModule[L]:
        return self._get_module(name)

    def has_module(self, name: ConfigurationModuleName) -> bool:
        if self.modules is MISSING:
            return False
        return any(m.name == name for m in self.modules)

    @property
    def device_settings(self) -> DeviceSettingsConfigurationModule:
        m = self._get_module(ConfigurationModuleName.DEVICE_SETTINGS)
        if not isinstance(m, DeviceSettingsConfigurationModule):
            raise _wrong_module(m, DeviceSettingsConfigurationModule)
        return m

    @property
    def qos(self) -> QosConfigurationModule[L]:
        m = self._get_module(ConfigurationModuleName.QOS)
        if not isinstance(m, QosConfigurationModule):
            raise _wrong_module(m, QosConfigurationModule)
        return m

    @property
    def firewall(self) -> FirewallConfigurationModule[L]:
        m = self._get_module(ConfigurationModuleName.FIREWALL)
        if not isinstance(m, FirewallConfigurationModule):
            raise _wrong_module(m, FirewallConfigurationModule)
        return m

    @property
    def wan(self) -> WanConfigurationModule:
        m = self._get_module(ConfigurationModuleName.WAN)
        if not isinstance(m, WanConfigurationModule):
            raise _wrong_module(m, WanConfigurationModule)
        return m

    @property
    def control_plane(self) -> ControlPlaneConfigurationModule:
        m = self._get_module(ConfigurationModuleName.CONTROL_PLANE)
        if not isinstance(m, ControlPlaneConfigurationModule):
            raise _wrong_module(m, ControlPlaneConfigurationModule)
        return m


class EdgeConfiguration(ConfigurationBase[Edge]):
    _config_context: ConfigContext | None = PrivateAttr(default=ConfigContext.EDGE)


class EnterpriseConfigurationProfile(ConfigurationBase[Profile]):
    _config_context: ConfigContext | None = PrivateAttr(default=ConfigContext.PROFILE)


EMPTY_EDGE_MODULE_DATA: dict[ConfigurationModuleName, dict[str, Any]] = {
    ConfigurationModuleName.FIREWALL: {"segments": []},
    ConfigurationModuleName.QOS: {"segments": []},
}


def new_edge_module(
    name: ConfigurationModuleName, configuration_id: int
) -> FirewallConfigurationModule[Edge] | QosConfigurationModule[Edge]:
    """Build an empty edge-layer module that does not exist on the VCO yet."""
    if name not in EMPTY_EDGE_MODULE_DATA:
        raise ValueError(
            f"Module {name.value!r} cannot be inserted as an empty edge layer"
        )
    now = dt.datetime.now(dt.UTC)
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
        FirewallConfigurationModule[Edge]
        if name is ConfigurationModuleName.FIREWALL
        else QosConfigurationModule[Edge]
    ).model_validate(row)
    module._is_new = True  # pyright: ignore[reportPrivateUsage]
    return module


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


class ConfigurationModuleUpdate(VcoModel):
    """The ``_update`` payload: raw module data, and refs when they are replaced."""

    data: dict[str, Any]
    refs: dict[str, Any] | MISSING = MISSING


class UpdateConfigurationModule(VcoModel):
    configuration_module_id: Annotated[int, Alias("id")]
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    version: Annotated[str | MISSING, NoneIsMissing] = MISSING
    update: Annotated[ConfigurationModuleUpdate, Alias("_update")]
    return_data: bool | MISSING = MISSING
    """``True`` asks for the module row as stored in place of a row count."""


class InsertConfigurationModuleResult(VcoModel):
    id_: Annotated[int, Alias("id")]
    rows: int | None | MISSING = MISSING
    error: str | None | MISSING = MISSING


def _module_names() -> frozenset[str]:
    """The ``name`` of each module class of ``AnyConfigurationModule``, whatever the layer.

    The alias stays a plain union with a layer variable in its QoS and firewall
    members (``QosConfigurationModule[X]``), and those classes have their ``name``
    as any other.
    """
    union = cast(
        "object", get_args(cast("object", AnyConfigurationModule.__value__))[0]
    )
    modules = cast("tuple[type[ConfigurationModuleBase], ...]", get_args(union))
    return frozenset(
        name
        for module in modules
        for name in cast(
            "tuple[str, ...]", get_args(module.model_fields["name"].annotation)
        )
    )


_MODULE_NAMES = _module_names()


def _module_or_count(value: object) -> str:
    """``"module"`` for a module or a row whose ``name`` the models know; else ``"count"``.

    The answer to a write is told by its shape, not by which form validates, so
    a module row that fails validation raises its own error, as on any read.
    """
    if isinstance(value, ConfigurationModuleBase):
        return "module"
    if _is_mapping(value):
        name = value.get("name")
        if isinstance(name, str) and name in _MODULE_NAMES:
            return "module"
    return "count"


class UpdateConfigurationModuleResult(
    RootModel[
        Annotated[
            Annotated[AnyConfigurationModule[L], Tag("module")]
            | Annotated[RowsModifiedConfirmation, Tag("count")],
            Discriminator(_module_or_count),
        ]
    ],
    Generic[L],  # noqa: UP046 (one shared constrained L, as a TypeVar)
):
    """An update's answer when it asks for data: the module row as stored, of the layer ``L``.

    A VCO that ignores ``returnData`` answers with a row count.
    """


class InsertedConfigurationModule(
    RootModel[
        Annotated[
            Annotated[AnyConfigurationModule[L], Tag("module")]
            | Annotated[InsertConfigurationModuleResult, Tag("count")],
            Discriminator(_module_or_count),
        ]
    ],
    Generic[L],  # noqa: UP046 (one shared constrained L, as a TypeVar)
):
    """An insert's answer when it asks for data: the module row as stored, of the layer ``L``.

    A VCO that ignores ``returnData`` answers with the new ID and a row count.
    """


class InsertConfigurationModuleBase(VcoModel):
    description: str | None = ""
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, lambda i: i.id)]
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    type_: Annotated[ConfigurationScope, Alias("type")] = ConfigurationScope.ENTERPRISE
    return_data: bool | MISSING = MISSING
    """``True`` asks for the module row as stored in place of its ID."""


class InsertQosConfigurationModule(InsertConfigurationModuleBase):
    name: Literal["QOS"] = "QOS"
    data: dict[str, Any]
    refs: dict[str, Any] | MISSING = MISSING


class InsertFirewallConfigurationModule(InsertConfigurationModuleBase):
    name: Literal["firewall"] = "firewall"
    data: dict[str, Any]


type InsertConfigurationModule = Annotated[
    InsertQosConfigurationModule | InsertFirewallConfigurationModule,
    Discriminator(discriminator="name"),
]


class GetConfigurationModules(VcoModel):
    enterprise_id: int | MISSING = MISSING
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, lambda i: i.id)]
    no_data: bool | MISSING = MISSING
    modules: list[ConfigurationModuleName] | MISSING = MISSING


class ConfigurationModuleVersion(VcoModel):
    """A module row as ``getConfigurationModules`` answers with ``noData``: its identity and ``version``, with no data and no refs."""

    id_: Annotated[int, Alias("id")]
    name: str
    version: str | None


class GetConfiguration(VcoModel):
    id_: Annotated[int, Alias("id"), Resolvable(ProfileIdentity, lambda i: i.id)] = (
        Field(description="Configuration ID")
    )
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[GetConfigurationWith] | MISSING, Alias("with")] = MISSING


class GetIdentifiableApplications(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, lambda i: i.id)] = (
        MISSING
    )
    application_ids: list[int] | MISSING = MISSING


class GetRoutableApplications(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, lambda i: i.id)] = (
        MISSING
    )


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

    id_: Annotated[int, Alias("id"), Resolvable(ProfileIdentity, lambda i: i.id)]
    enterprise_id: int | MISSING = MISSING


__all__ = [
    "EMPTY_EDGE_MODULE_DATA",
    "AnyConfigurationModule",
    "AnyStoredModule",
    "Configuration",
    "ConfigurationBase",
    "ConfigurationModuleBase",
    "ConfigurationModuleUpdate",
    "ConfigurationModuleVersion",
    "ControlPlaneConfigurationModule",
    "DeleteConfiguration",
    "DeviceSettingsConfigurationModule",
    "EdgeConfiguration",
    "EnterpriseConfigurationModule",
    "EnterpriseConfigurationProfile",
    "FirewallConfigurationModule",
    "GetAnalyticsConfiguration",
    "GetAnalyticsConfigurationResult",
    "GetApplicationsResult",
    "GetConfiguration",
    "GetConfigurationModules",
    "GetIdentifiableApplications",
    "GetRoutableApplications",
    "InsertConfigurationModule",
    "InsertConfigurationModuleResult",
    "InsertedConfigurationModule",
    "OperatorConfiguration",
    "OperatorConfigurationModule",
    "QosConfigurationModule",
    "UpdateConfigurationModule",
    "UpdateConfigurationModuleResult",
    "WanConfigurationModule",
    "new_edge_module",
]
