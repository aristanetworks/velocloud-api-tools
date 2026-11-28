"""Configuration secrets stay out of the text of errors, plans and messages."""

import contextlib
import copy
import json
import re
import traceback
import types
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal, cast, get_args

import pytest
import pyvelo.core
from pydantic import MISSING, Discriminator, Field, ValidationError
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import (
    ModuleWriteError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.models.configuration.control_plane.root import ControlPlaneData
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.models.enterprise import service_data
from pyvelo.client.module_check import ModuleWrite, check_module_write
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    GetConfiguration,
    WanConfigurationModule,
)
from pyvelo.core import (
    Alias,
    DefaultingModel,
    Key,
    KeyField,
    NamedDocumentError,
    PatchError,
    Path,
    Positional,
    Secret,
    VcoModel,
    add,
    hide_secrets,
    is_secret,
    list_kind,
    restore_secrets,
    shown_value,
)
from pyvelo.core.base import is_dict
from pyvelo.core.layers import Edge, field_kind
from pyvelo.core.merge import (
    _list_spec,  # pyright: ignore[reportPrivateUsage]
    _validate_field,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.core.names import named_value
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.core.secrets import declares_secret
from pyvelo.core.show import shown, shown_together
from pyvelo.testing import ModuleStore, as_stored, make_module_json, store_of

from ._type_support import MockVcoClientFactory, require_present

OLD, NEW, OTHER = "OLD-md5-made-up", "NEW-md5-made-up", "THIRD-md5-made-up"
ROOTS = (
    FirewallData[Edge],
    QosData[Edge],
    DeviceSettingsData,
    WanData,
    ControlPlaneData,
)


def _models_in(annotation: object) -> Iterator[type[VcoModel]]:
    """Every model class named anywhere inside an annotation."""
    if isinstance(annotation, type) and issubclass(annotation, VcoModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _models_in(arg)


def reachable_classes() -> list[type[VcoModel]]:
    """Every model class the five module data classes reach, each once."""
    seen: list[type[VcoModel]] = []
    todo: list[type[VcoModel]] = list(ROOTS)
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.append(cls)
        for fi in cls.model_fields.values():
            todo.extend(_models_in(field_kind(fi).inner_type))
    return seen


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def neighbor(password: object = OLD) -> dict[str, Any]:
    """A BGP neighbour; the password is its last key.

    That is where an error's cut-off text shows it.
    """
    return {
        "neighborIp": "192.0.2.1",
        "neighborAS": "65001",
        "enableMd5": True,
        "id": "00000000-0000-0000-0000-0000000000aa",
        "md5Password": password,
    }


def settings(*neighbors: dict[str, Any]) -> dict[str, Any]:
    """Device settings data with one segment whose BGP has *neighbors*."""
    return {
        "lan": {"networks": [], "interfaces": []},
        "segments": [
            {
                "segment": desc(0, "Global Segment"),
                "bgp": {
                    "enabled": True,
                    "override": True,
                    "ASN": "65000",
                    "neighbors": list(neighbors),
                },
            }
        ],
    }


def ds(raw: dict[str, Any]) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", copy.deepcopy(raw), configuration_id=10)
    )


async def test_a_row_that_does_not_parse_raises_without_its_values(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = ModuleStore()
    bad = {k: v for k, v in neighbor().items() if k != "neighborAS"}
    store.add_configuration(
        {
            "id": 10,
            "name": "Edge Specific Profile",
            "modules": [
                make_module_json(
                    "deviceSettings", settings(bad), module_id=11, configuration_id=10
                )
            ],
        }
    )
    client = await mock_vco_client(store)
    request = GetConfiguration(id_=10, with_=[GetConfigurationWith.MODULES])
    with pytest.raises(ValidationError) as exc:
        _ = await client.get_configuration(request)
    text = "".join(traceback.format_exception(exc.value))
    assert "neighborAS" in text
    assert "Field required" in text
    assert OLD not in text
    assert "input_value" not in text
    # The values are still there for a program that asks for them.
    assert exc.value.errors()[0]["input"]["md5Password"] == OLD


async def test_a_write_whose_answer_does_not_parse_raises_without_its_values(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    def without_its_as(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
        del new["data"]["segments"][0]["bgp"]["neighbors"][0]["neighborAS"]

    module = ds(settings(neighbor()))
    require_present(module.data.segments[0].bgp).asn = "65010"
    store = store_of(module)
    store.on_store.append(without_its_as)
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert isinstance(exc.value.__cause__, ValidationError)
    # The traceback prints the cause under the error.
    text = "".join(traceback.format_exception(exc.value))
    assert "neighborAS" in text
    assert "Field required" in text
    assert OLD not in text
    assert "input_value" not in text


async def test_a_write_whose_row_read_back_does_not_parse_raises_without_its_values(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    def without_its_as(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
        del new["data"]["segments"][0]["bgp"]["neighbors"][0]["neighborAS"]

    module = ds(settings(neighbor()))
    require_present(module.data.segments[0].bgp).asn = "65010"
    store = store_of(module, return_data=False)
    store.on_store.append(without_its_as)
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.detail == "the module row read back does not parse"
    cause = exc.value.__cause__
    assert isinstance(cause, ValidationError)
    text = "".join(traceback.format_exception(exc.value))
    assert "neighborAS" in text
    assert "Field required" in text
    assert OLD not in text
    assert "input_value" not in text
    assert cause.errors()[0]["input"]["md5Password"] == OLD


class TaggedA(VcoModel):
    kind: Literal["a"] = "a"
    secret: str


class TaggedB(VcoModel):
    kind: Literal["b"] = "b"
    secret: str


class Inner(VcoModel):
    secret: str = "s"


class Holder(VcoModel):
    name: str = "x"
    inner: Inner = Field(default_factory=Inner)
    defaulted: Annotated[Inner, DefaultingModel(Inner)] = Field(default_factory=Inner)
    tagged: list[Annotated[TaggedA | TaggedB, Discriminator("kind")]] = Field(
        default_factory=list
    )
    ports: list[int] = Field(default_factory=list)
    by_number: dict[int, TaggedA] = Field(default_factory=dict)


def assert_chain_quotes_no_input(error: BaseException) -> None:
    """Neither the cause nor the context of *error* quotes an input value.

    The context is empty.
    """
    assert error.__context__ is None
    for linked in (error.__cause__, error.__context__):
        if linked is not None:
            assert isinstance(linked, ValidationError)
            for text in (str(linked), repr(linked)):
                assert "input_value" not in text
                assert OLD not in text


@pytest.mark.parametrize(
    "patch",
    [
        {"name": {"v": OLD}},
        {"tagged": {"+": {"_op": "add", "kind": "a", "secret": {"v": OLD}}}},
        {"tagged": [{"kind": "a", "secret": {"v": OLD}}]},
        {"ports": {"x": {"_op": "add"}}},
        {"by_number": {"x": {"_op": "add", "secret": "s"}}},
    ],
)
def test_the_cause_of_a_patch_error_does_not_quote_the_patch(
    patch: dict[str, object],
) -> None:
    with pytest.raises(PatchError) as exc:
        _ = Holder().merge_patch(patch)
    cause = exc.value.__cause__
    assert isinstance(cause, ValidationError)
    for text in (str(cause), repr(cause)):
        assert "input_value" not in text
        assert OLD not in text
    assert_chain_quotes_no_input(exc.value)


@pytest.mark.parametrize("field", ["inner", "defaulted"])
@pytest.mark.parametrize("wrap", [False, True])
def test_a_field_typed_as_a_model_does_not_quote_a_value_that_is_not_one(
    field: str, *, wrap: bool
) -> None:
    value: object = [OLD] if wrap else OLD
    with pytest.raises(PatchError) as exc:
        _ = Holder().merge_patch({field: value})
    cause = exc.value.__cause__
    assert isinstance(cause, ValidationError)
    for text in (str(cause), repr(cause), str(exc.value)):
        assert "input_value" not in text
        assert OLD not in text
    assert cause.errors()[0]["input"] == value
    assert_chain_quotes_no_input(exc.value)


def test_no_field_of_the_models_quotes_a_patch_value_in_the_cause_of_its_error() -> (
    None
):
    """Give every field the module data classes reach a text.

    It is a text that none of them accepts.
    """
    refused = 0
    for cls in reachable_classes():
        for name in cls.model_fields:
            try:
                _ = _validate_field(cls, name, OLD, Path(()))
            except PatchError as error:
                cause = error.__cause__
                assert isinstance(cause, ValidationError)
                assert OLD not in f"{cause}{cause!r}", f"{cls.__name__}.{name}"
                assert_chain_quotes_no_input(error)
                refused += 1
    assert refused > 1000


def test_every_list_field_still_builds_its_adapters() -> None:
    """No list or map field of the models refuses the setting.

    The setting is on the adapters built for it.
    """
    built = 0
    for cls in reachable_classes():
        for fi in cls.model_fields.values():
            if list_kind(fi) is None:
                continue
            spec = _list_spec(fi)
            assert spec is not None
            if spec.item_type is not None:
                # The first call builds the adapter of a union of models.
                with contextlib.suppress(ValidationError):
                    _ = spec.item_type.validate({})
            built += 1
    assert built > 100


SEGMENT = ("segments", Key("Global Segment"))
NEIGHBOR = Path((*SEGMENT, "bgp", "neighbors", Key("192.0.2.1")))


class Marked(VcoModel):
    plain: str = ""
    password: Annotated[str, Secret()] = ""


def test_is_secret() -> None:
    assert is_secret(Marked.model_fields["password"]) is True
    assert is_secret(Marked.model_fields["plain"]) is False


# The fields of the module data that hold a secret. Marking one, or adding a
# field whose name looks like one, means changing one of these two sets.
SECRET_FIELDS = frozenset(
    {
        "BgpNeighbor.md5_password",
        "CssIpsecSiteData.psk",
        "EdgeSnmpV2c.community",
        "EdgeSnmpV2c.community_list",
        "LanWirelessInterface.passphrase",
        "Md5AuthKey.key",
        "RoutedInterfaceAddressing.password",
        "RoutedInterfaceAddressingV6.password",
        "RoutedInterfaceCellular.password",
        "RoutedInterfaceCellular.sim_pin",
        "RoutedInterfaceOspf.authentication_passphrase",
        "SnmpV3User.passphrase",
        "WanNetwork.isp_key",
        "WanNetwork.password",
    }
)
NOT_SECRET = frozenset(
    {
        "BgpFilterActionValue.community_additive",
        "CssIpsecSiteData.psk_type",
        "Md5AuthKey.key_number",
        "SegmentBgp.uplink_community",
    }
)
LOOKS_SECRET = re.compile(
    r"passw|passphrase|secret|psk|community|(^|_)key($|_)|token|(^|_)pin($|_)|credential"
)


def reachable_fields() -> dict[str, bool]:
    """``"Class.field"`` to whether it is marked.

    For every class the module data classes reach.
    """
    return {
        f"{cls.__name__}.{name}": is_secret(fi)
        for cls in reachable_classes()
        for name, fi in cls.model_fields.items()
    }


def test_the_secret_fields_of_the_module_data_are_the_recorded_set() -> None:
    fields = reachable_fields()
    assert frozenset(name for name, marked in fields.items() if marked) == SECRET_FIELDS
    looks = {name for name in fields if LOOKS_SECRET.search(name.split(".")[1])}
    assert looks - SECRET_FIELDS == NOT_SECRET


def test_the_credentials_of_enterprise_services_are_marked() -> None:
    marked = {
        f"{cls.__name__}.{name}"
        for cls in vars(service_data).values()
        if isinstance(cls, type) and issubclass(cls, VcoModel)
        for name, fi in cls.model_fields.items()
        if name in cls.__annotations__ and is_secret(fi)
    }
    assert marked == {
        "RadiusServer.secret",
        "TacacsServiceData.shared_secret",
        "AzureVendorSpecificData.client_secret",
        "ZscalerVendorSpecificData.api_token",
        "ZscalerVendorSpecificData.password",
        "AwsVendorSpecificData.access_key",
        "AwsVendorSpecificData.secret_key",
        "AwsVendorSpecificData.session_token",
        "SseVendorSpecificData.password",
        "LocalUiCredentialsData.password",
        "BaseCloudSecurityCredentials.password",
        "ZscalerCloudSecurityCredentials.api_token",
    }


@pytest.mark.parametrize(
    ("path", "value", "hidden"),
    [
        (Path((*NEIGHBOR.parts, "md5Password")), OLD, "***"),
        # A path written with the python name of the field.
        (Path((*NEIGHBOR.parts, "md5_password")), OLD, "***"),
        (Path((*NEIGHBOR.parts, "md5Password")), MISSING, MISSING),
        # A whole item, as the value of an added neighbour is.
        (
            NEIGHBOR,
            {"neighborIp": "192.0.2.1", "md5Password": OLD},
            {"neighborIp": "192.0.2.1", "md5Password": "***"},
        ),
        # The whole document; a key the models do not know is left as it is.
        (
            Path(()),
            {
                "snmp": {
                    "snmpv2c": {
                        "community": "c",
                        "communityList": ["a"],
                        "allowedIp": ["10.0.0.1"],
                    }
                },
                "unknown": {"password": "kept"},
            },
            {
                "snmp": {
                    "snmpv2c": {
                        "community": "***",
                        "communityList": "***",
                        "allowedIp": ["10.0.0.1"],
                    }
                },
                "unknown": {"password": "kept"},
            },
        ),
        # Below a secret field.
        (Path(("snmp", "snmpv2c", "communityList", 0)), "a", "***"),
        # An item of a list without a key.
        (
            Path(("snmp", "snmpv3", "users", 0)),
            {"name": "u", "passphrase": "p"},
            {"name": "u", "passphrase": "***"},
        ),
        # An item of a list of a union: a field of any variant counts.
        (
            Path(("lan", "interfaces", 0)),
            {"name": "WLAN1", "type": "wireless", "ssid": "s", "passphrase": "p"},
            {"name": "WLAN1", "type": "wireless", "ssid": "s", "passphrase": "***"},
        ),
        # A map of models: its keys are not field names.
        (
            Path(("models",)),
            {
                "edge510": {
                    "routedInterfaces": [
                        {
                            "name": "GE3",
                            "addressing": {"password": "p"},
                            "ospf": {"authPassphrase": "p"},
                        }
                    ]
                }
            },
            {
                "edge510": {
                    "routedInterfaces": [
                        {
                            "name": "GE3",
                            "addressing": {"password": "***"},
                            "ospf": {"authPassphrase": "***"},
                        }
                    ]
                }
            },
        ),
        (
            Path(
                (
                    "models",
                    "edge510",
                    "routedInterfaces",
                    Key("GE3"),
                    "addressing",
                    "password",
                )
            ),
            "p",
            "***",
        ),
        (
            Path(("ntpServer",)),
            {"keys": [{"keyNumber": 1, "key": "k"}]},
            {"keys": [{"keyNumber": 1, "key": "***"}]},
        ),
        (Path((*SEGMENT, "bgp", "ASN")), "65000", "65000"),
        (Path(("nope", "password")), "kept", "kept"),
    ],
)
def test_hide_secrets(path: Path, value: object, hidden: object) -> None:
    given = copy.deepcopy(value)
    assert hide_secrets(DeviceSettingsData, path, value) == hidden
    assert value == given  # the value given is not changed


def test_hide_secrets_without_a_model_hides_nothing() -> None:
    assert hide_secrets(None, Path(("a", "password")), "kept") == "kept"


def rotated() -> DeviceSettingsConfigurationModule:
    """Device settings whose one BGP neighbour gets a new password."""
    module = ds(settings(neighbor(OLD)))
    bgp = require_present(module.data.segments[0].bgp)
    bgp.neighbors[0].md5_password = NEW
    return module


def test_shown_hides_the_secrets_of_the_model_at_the_path() -> None:
    path = Path((*NEIGHBOR.parts, "md5Password"))
    assert shown(OLD) == f'"{OLD}"'
    assert shown(OLD, model=DeviceSettingsData, path=path) == '"***"'
    assert shown_together(OLD, MISSING, NEW, model=DeviceSettingsData, path=path) == (
        '"***"',
        "(nothing)",
        '"***"',
    )
    item = {"neighborIp": "192.0.2.1", "md5Password": OLD}
    assert shown(item, model=DeviceSettingsData, path=NEIGHBOR) == (
        '{"md5Password": "***", "neighborIp": "192.0.2.1"}'
    )


def test_a_plan_shows_no_secret_and_keeps_it_on_the_change() -> None:
    plan = plan_module(rotated())
    (change,) = plan.changes
    assert (change.before, change.after) == (OLD, NEW)
    text = plan.render()
    assert OLD not in text
    assert NEW not in text
    assert f'{NEIGHBOR}.md5_password: "***" -> "***"' in text
    assert plan.model is DeviceSettingsData


def test_a_plan_made_from_a_dict_hides_secrets_too() -> None:
    module = ds({**settings(), "snmp": {"snmpv2c": {"community": OLD}}})
    plan = plan_module(module, {**settings(), "snmp": {"snmpv2c": {"community": NEW}}})
    assert plan.model is DeviceSettingsData
    text = plan.render()
    assert OLD not in text
    assert NEW not in text
    assert 'snmp.v2c.community: "***" -> "***"' in text


def test_a_shown_change_hides_the_secret_and_the_change_keeps_it() -> None:
    plan = plan_module(rotated())
    (change,) = plan.changes
    seen = plan.shown_change(change)
    assert (change.before, change.after) == (OLD, NEW)
    assert (seen.before, seen.after) == ("***", "***")


def neighbor_added() -> DeviceSettingsConfigurationModule:
    """Device settings with a BGP neighbour added, its password ``NEW``."""
    module = ds(settings(neighbor(OLD)))
    bgp = require_present(module.data.segments[0].bgp)
    _ = bgp.merge_patch(
        {
            "neighbors": {
                "+": add(
                    {
                        "neighbor_ip": "192.0.2.9",
                        "neighbor_as": "65002",
                        "md5_password": NEW,
                    }
                )
            }
        }
    )
    return module


def test_a_shown_change_hides_a_secret_inside_an_added_item() -> None:
    plan = plan_module(neighbor_added())
    shown = [plan.shown_change(change) for change in plan.changes]
    assert NEW in json.dumps([change.after for change in plan.changes], default=str)
    assert NEW not in json.dumps([seen.after for seen in shown], default=str)
    assert any(
        is_dict(seen.after) and seen.after.get("md5_password") == "***"
        for seen in shown
    )


@pytest.mark.parametrize("module", [rotated, neighbor_added])
def test_a_shown_change_holds_what_the_plan_line_prints(
    module: Callable[[], DeviceSettingsConfigurationModule],
) -> None:
    """Printed with no model to find a secret by, a shown change is its line.

    The line hides the secrets of the shown values by the model and the path
    (a line of an added item is cut before its password): in a shown change
    there is none left to hide.
    """
    plan = plan_module(module())
    lines: list[str] = []
    for change in plan.changes:
        seen = plan.shown_change(change)
        for value in (seen.before, seen.after):
            assert hide_secrets(plan.model, change.path, value) == value
        if seen.before is MISSING:
            place = "" if seen.place is None else f" ({seen.place})"
            lines.append(f"  + {seen.path}{place}: {shown(seen.after)}")
        else:
            old, new = shown_together(seen.before, seen.after)
            lines.append(f"  ~ {seen.path}: {old} -> {new}")
    printed = plan.render().splitlines()
    assert [line for line in printed if line.startswith(("  + ", "  ~ "))] == lines


def test_the_repr_of_a_plan_and_of_a_write_hold_no_data() -> None:
    module = rotated()
    plan = plan_module(module)
    assert repr(plan) == (
        "ModulePlan(name='deviceSettings', operation='update', changes=1)"
    )
    write = check_module_write(module, plan, as_stored(module, plan.data))
    assert isinstance(write, ModuleWrite)
    assert repr(write) == (
        "ModuleWrite(name='deviceSettings', operation='update', applied=1, "
        "left_out=0, server_changes=0)"
    )


def test_a_write_error_shows_no_secret_and_keeps_it_on_the_change() -> None:
    module = rotated()
    stored = as_stored(module, settings(neighbor(OTHER)))
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), stored)
    (lost,) = exc.value.lost_edits
    assert (lost.as_read, lost.intended, lost.stored) == (OLD, NEW, OTHER)
    text = "".join(traceback.format_exception(exc.value))
    assert not any(secret in text for secret in (OLD, NEW, OTHER))
    assert f"{NEIGHBOR}.md5_password" in text
    assert text.count("***") == 3


def test_a_lost_secret_the_redo_leaves_out_is_shown_hidden_and_kept_in_full() -> None:
    """A clash on a neighbour's password: another writer stored ``OTHER``."""
    module = rotated()
    stored = as_stored(module, settings(neighbor(OTHER)))
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), stored)
    error = exc.value
    (lost,) = error.lost_edits
    assert (lost.as_read, lost.intended, lost.stored) == (OLD, NEW, OTHER)
    (left_out,) = error.not_redone()
    assert str(left_out.path) == f"{NEIGHBOR}.md5_password"
    assert (left_out.as_read, left_out.intended, left_out.stored) == ("***",) * 3
    patch = error.redo_patch(include_clashes=True)
    assert NEW in json.dumps(patch)
    assert "***" not in json.dumps(patch)
    assert error.not_redone(include_clashes=True) == ()


def test_a_write_error_hides_a_secret_under_a_key_it_shows_by_attribute() -> None:
    """A lost added neighbour, and one only the VCO has: objects with a password.

    The line writes the object's keys by attribute name (``md5_password``),
    and the secret is found under that name too.
    """
    module = ds(settings(neighbor(OLD)))
    bgp = require_present(module.data.segments[0].bgp)
    added = {**neighbor(NEW), "neighborIp": "192.0.2.2"}
    bgp.neighbors.append(type(bgp.neighbors[0]).model_validate(added))
    bgp.asn = "65010"
    stored = settings(neighbor(OLD), {**neighbor(OTHER), "neighborIp": "192.0.2.9"})
    stored["segments"][0]["bgp"]["ASN"] = "65010"
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), as_stored(module, stored))
    error = exc.value
    (lost,) = error.lost_edits
    assert isinstance(lost.intended, dict)
    assert lost.intended["md5Password"] == NEW
    text = error.render(limit=None)
    assert not any(secret in text for secret in (OLD, NEW, OTHER))
    assert "md5Password" not in text
    named = named_value(lost.intended, DeviceSettingsData, lost.path)
    assert isinstance(named, dict)
    assert named["md5_password"] == NEW
    hidden = hide_secrets(DeviceSettingsData, lost.path, named)
    assert isinstance(hidden, dict)
    assert hidden["md5_password"] == "***"


def test_a_write_error_hides_a_secret_in_an_object_its_line_shows_whole() -> None:
    """An object of one key, the password: short enough that the line is not cut.

    The lines of the test above are cut before the password, so their text
    alone would read the same with nothing hidden.
    """
    change = ServerChange(NEIGHBOR, {"md5Password": OLD}, {"md5Password": OTHER})
    error = ModuleWriteError(
        10,
        "v1",
        module_name="deviceSettings",
        plan=plan_module(rotated()),
        server_changes=[change],
    )
    assert error.render().splitlines()[-1] == (
        '  segments["Global Segment"].bgp.neighbors["192.0.2.1"]: '
        'read {"md5_password": "***"}, stored {"md5_password": "***"}'
    )
    assert (change.as_read, change.stored) == (
        {"md5Password": OLD},
        {"md5Password": OTHER},
    )


def test_a_write_error_hides_what_another_writer_stored() -> None:
    module = ds(settings(neighbor(OLD)))
    require_present(module.data.segments[0].bgp).asn = "65010"
    stored = as_stored(module, settings(neighbor(OTHER)))
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), stored)
    assert [str(c.path) for c in exc.value.server_changes] == [
        f"{NEIGHBOR}.md5Password"
    ]
    assert OLD not in str(exc.value)
    assert OTHER not in str(exc.value)


def test_a_write_error_does_not_show_a_ref_row() -> None:
    module = ds(settings())
    row = {"id": 101, "enterpriseObjectId": 5, "data": {"sharedSecret": OLD}}
    lost = PlannedChange(Path.parse("auth[5]"), MISSING, row, MISSING, part="refs")
    error = WriteNotAppliedError(
        1,
        "v1",
        module_name="deviceSettings",
        plan=plan_module(module),
        module=module,
        lost_edits=[lost],
    )
    (line,) = [line for line in str(error).splitlines() if "auth[5]" in line]
    assert OLD not in line
    assert "(nothing)" in line
    assert "***" in line


def test_a_lost_ref_row_the_redo_leaves_out_is_shown_as_its_line_shows_it() -> None:
    module = ds(settings())
    row = {"id": 101, "enterpriseObjectId": 5, "data": {"sharedSecret": OLD}}
    lost = PlannedChange(Path.parse("auth[5]"), OTHER, row, NEW, part="refs")
    error = WriteNotAppliedError(
        1,
        "v1",
        module_name="deviceSettings",
        plan=plan_module(module),
        module=module,
        lost_edits=[lost],
    )
    (left_out,) = error.not_redone()
    assert (left_out.path, left_out.part) == (lost.path, "refs")
    assert (left_out.as_read, left_out.intended, left_out.stored) == ("***",) * 3
    assert error.lost_edits == (lost,)
    unset = PlannedChange(Path.parse("auth[5]"), MISSING, row, MISSING, part="refs")
    error.lost_edits = (unset,)
    (left_out,) = error.not_redone()
    assert (left_out.as_read, left_out.intended, left_out.stored) == (
        MISSING,
        "***",
        MISSING,
    )


def test_an_error_built_without_its_plan_shows_its_data_entries_as_they_are() -> None:
    # pyvelo builds each write error that has lost edits with its plan. One
    # built by hand without it has no model to find a secret by: a data entry
    # is shown, in the message and in ``not_redone()``, as it is.
    lost = PlannedChange(Path.parse("segments[#0].bgp.md5Password"), OLD, NEW, OTHER)
    row = {"id": 101, "enterpriseObjectId": 5, "data": {"sharedSecret": OLD}}
    lost_row = PlannedChange(Path.parse("auth[5]"), MISSING, row, MISSING, part="refs")
    error = ModuleWriteError(
        1, "v1", module_name="deviceSettings", lost_edits=[lost, lost_row]
    )
    text = str(error)
    assert f'md5Password: read "{OLD}", meant "{NEW}", stored "{OTHER}"' in text
    assert error.not_redone()[0] == lost
    # A ref row is still "***" whole: no model is needed to know a row.
    assert 'refs auth[5]: read (nothing), meant "***", stored (nothing)' in text
    assert error.not_redone()[1].intended == "***"


def test_an_unknown_outcome_shows_no_secret_of_what_it_sent() -> None:
    error = WriteOutcomeUnknownError(
        1,
        "v1",
        "its configuration does not hold the module",
        answer="counted",
        module_name="deviceSettings",
        plan=plan_module(rotated()),
    )
    text = str(error)
    assert OLD not in text
    assert NEW not in text
    assert 'md5_password: read "***", meant "***"' in text


def rule(n: int) -> dict[str, Any]:
    return {
        "name": f"rule-{n:03d}",
        "ruleLogicalId": f"00000000-0000-0000-0000-{n:012d}",
        "action": {"allow_or_deny": "allow"},
        "match": {"dport_low": 1000 + n, "dport_high": 1000 + n},
        "loggingEnabled": False,
    }


def firewall(n: int) -> dict[str, Any]:
    return {
        "segments": [
            {
                "segment": desc(0, "Global Segment"),
                "outbound": [rule(i) for i in range(n)],
            }
        ]
    }


def logging_on_500_rules() -> FirewallConfigurationModule[Edge]:
    module = FirewallConfigurationModule[Edge].model_validate(
        make_module_json("firewall", firewall(500), configuration_id=10)
    )
    for item in module.data.segments[0].outbound:
        item.logging_enabled = True
    return module


def entries(text: str, key: str) -> int:
    """How many lines of *text* are the entry of a path that ends in *key*."""
    return sum(1 for line in text.splitlines() if key in line)


def test_the_message_of_a_write_error_is_bounded() -> None:
    module = logging_on_500_rules()
    other = firewall(500)
    for item in other["segments"][0]["outbound"]:
        item["match"]["dport_high"] += 1
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), as_stored(module, other))
    error = exc.value
    assert (len(error.lost_edits), len(error.server_changes)) == (500, 500)
    text = str(error)
    assert len(text) < 10_000
    assert entries(text, ".logging_enabled:") == 20
    assert entries(text, ".match.destination_port_high:") == 20
    assert "  and 480 more: error.lost_edits has them all" in text.splitlines()
    assert "  and 480 more: error.server_changes has them all" in text.splitlines()
    assert entries(error.render(limit=None), ".logging_enabled:") == 500
    assert " more: " not in error.render(limit=None)
    assert entries(error.render(limit=1), ".logging_enabled:") == 1


def test_the_message_of_an_unknown_outcome_is_bounded() -> None:
    error = WriteOutcomeUnknownError(
        1,
        "v1",
        "its configuration does not hold the module",
        answer="counted",
        module_name="firewall",
        plan=plan_module(logging_on_500_rules()),
    )
    text = str(error)
    assert entries(text, ".logging_enabled:") == 20
    assert "  and 480 more: error.plan.changes has them all" in text.splitlines()
    assert entries(error.render(limit=None), ".logging_enabled:") == 500


def test_hide_secrets_walks_a_tuple_as_it_walks_a_list() -> None:
    item = ({"neighborIp": "192.0.2.1", "md5Password": OLD},)
    path = Path((*SEGMENT, "bgp", "neighbors"))
    assert hide_secrets(DeviceSettingsData, path, item) == [
        {"neighborIp": "192.0.2.1", "md5Password": "***"}
    ]
    assert hide_secrets(
        DeviceSettingsData, Path(("snmp", "snmpv2c")), {"communityList": ("a", "b")}
    ) == {"communityList": "***"}


def test_hide_secrets_does_not_walk_an_object_that_can_hold_no_secret() -> None:
    # A firewall of thousands of rules costs nothing to show.
    module = FirewallConfigurationModule[Edge].model_validate(
        make_module_json("firewall", firewall(2), configuration_id=10)
    )
    model = type(module.data)
    assert not declares_secret(model)
    raw = firewall(2)
    assert hide_secrets(model, Path(()), raw) is raw
    # A tuple is still read as the list it is.
    segments = tuple(raw["segments"])
    hidden = hide_secrets(model, Path(("segments",)), segments)
    assert hidden == raw["segments"]
    assert cast("list[object]", hidden)[0] is segments[0]


NETWORK_ID = "00000000-0000-0000-0000-0000000000b1"


def test_a_wan_network_s_isp_key_is_hidden_in_a_plan() -> None:
    network = {
        "internalId": NETWORK_ID,
        "logicalId": "00:00:00:00:00:02:0000",
        "name": "LTE",
        "interface": "CELL1",
        "type": "WIRELESS",
        "addressingVersion": "IPv4",
        "ispKey": OLD,
    }
    raw = {"links": [], "networks": [network]}
    module = WanConfigurationModule.model_validate(make_module_json("WAN", raw))
    assert plan_module(module).operation == "none"
    require_present(module.data.networks)[0].isp_key = NEW
    plan = plan_module(module)
    (change,) = plan.changes
    assert (change.before, change.after) == (OLD, NEW)
    text = plan.render()
    assert OLD not in text
    assert NEW not in text
    assert 'networks[LTE].isp_key: "***" -> "***"' in text
    path = Path(("networks", Key(f"{NETWORK_ID},IPv4"), "ispKey"))
    assert hide_secrets(WanData, path, OLD) == "***"


class Login(VcoModel):
    user_name: str = ""
    pass_word: Annotated[str | int | None, Secret()] = None


class Vault(VcoModel):
    login: Login | MISSING = MISSING
    logins: Annotated[list[Login], KeyField("user_name")] = Field(default_factory=list)
    spare_logins: list[Login] = Field(default_factory=list)
    by_site: dict[str, Login] = Field(default_factory=dict)
    words: Annotated[list[str], Positional(), Secret()] = Field(default_factory=list)
    free_form: dict[str, Any] = Field(default_factory=dict)


def login(name: str, secret: object) -> dict[str, object]:
    return {"userName": name, "passWord": secret}


STORED: dict[str, Any] = {
    "login": login("a", "s-a"),
    "logins": [login("b", "s-b"), login("c", "s-c")],
    "spareLogins": [login("d", "s-d"), login("e", "s-e")],
    "bySite": {"x": login("f", "s-f")},
    "words": ["w1", "w2"],
    "freeForm": {"passWord": "s-free"},
}


def masked() -> dict[str, Any]:
    return cast("dict[str, Any]", hide_secrets(Vault, Path(()), STORED))


def test_every_mask_takes_the_stored_value() -> None:
    assert masked()["login"] == login("a", "***")
    assert masked()["words"] == "***"
    assert restore_secrets(Vault, masked(), STORED) == STORED


def test_a_keyed_item_keeps_its_secret_wherever_it_moves() -> None:
    document = masked()
    document["logins"] = [login("new", "given"), *reversed(document["logins"])]
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, STORED))
    assert restored["logins"] == [
        login("new", "given"),
        login("c", "s-c"),
        login("b", "s-b"),
    ]


def test_a_secret_given_in_full_is_taken_as_given() -> None:
    document = masked()
    document["login"]["passWord"] = "typed"
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, STORED))
    assert restored["login"] == login("a", "typed")


@pytest.mark.parametrize("secret", [None, "", 0, 1234, "***"])
def test_a_secret_of_any_json_type_is_masked_and_restored(secret: object) -> None:
    stored = {"login": login("a", secret), "words": []}
    document = hide_secrets(Vault, Path(()), stored)
    assert document == {"login": login("a", "***"), "words": "***"}
    assert restore_secrets(Vault, document, stored) == stored


@pytest.mark.parametrize(
    ("document", "stored", "path", "why"),
    [
        (
            {"login": login("a", "***")},
            MISSING,
            "login.pass_word",
            "no `stored` was given",
        ),
        (
            {"login": login("a", "***")},
            {"login": {"userName": "a"}},
            "login.pass_word",
            "no value there",
        ),
        (
            {"logins": [login("zz", "***")]},
            STORED,
            "logins[#0].pass_word",
            "no such item",
        ),
        (
            {"bySite": {"y": login("f", "***")}},
            STORED,
            "by_site.y.pass_word",
            "no value there",
        ),
    ],
)
def test_a_mask_with_nothing_stored_at_its_place_is_refused(
    document: object, stored: object, path: str, why: str
) -> None:
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Vault, document, stored)
    assert (caught.value.kind, caught.value.path) == ("masked_secret", path)
    assert why in caught.value.message
    assert "would be written to the VCO as the secret" in caught.value.message


def test_an_item_of_a_list_without_a_key_keeps_its_secret_wherever_it_moves() -> None:
    document = masked()
    document["spareLogins"].reverse()
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, STORED))
    assert restored["spareLogins"] == [login("e", "s-e"), login("d", "s-d")]
    document = masked()
    document["spareLogins"] = document["spareLogins"][1:]
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, STORED))
    assert restored["spareLogins"] == [login("e", "s-e")]


def test_an_edited_item_of_a_list_without_a_key_must_restate_its_secret() -> None:
    document = masked()
    document["spareLogins"][0]["userName"] = "d2"
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Vault, document, STORED)
    assert (caught.value.kind, caught.value.path) == (
        "masked_secret",
        "spare_logins[#0].pass_word",
    )
    assert "matches no stored item" in caught.value.message
    assert "give the secret in full" in caught.value.message
    document["spareLogins"][0]["passWord"] = "s-new"
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, STORED))
    assert restored["spareLogins"][0] == login("d2", "s-new")


def test_a_mask_matching_two_stored_items_with_different_secrets_is_refused() -> None:
    stored = {**STORED, "spareLogins": [login("d", "s-1"), login("d", "s-2")]}
    document = {"spareLogins": [login("d", "***")]}
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Vault, document, stored)
    assert "matches 2 stored items" in caught.value.message
    same = {**STORED, "spareLogins": [login("d", "s-1"), login("d", "s-1")]}
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, same))
    assert restored["spareLogins"] == [login("d", "s-1")]


def test_an_item_matches_a_stored_item_as_a_save_compares_them() -> None:
    truth = {**login("d", "s-true"), "flag": True}
    one = {**login("d", "s-one"), "flag": 1}
    stored = {"spareLogins": [truth, one]}
    document = {"spareLogins": [{**login("d", "***"), "flag": 1.0}]}
    assert restore_secrets(Vault, document, stored) == {
        "spareLogins": [{**login("d", "s-one"), "flag": 1.0}]
    }


def test_the_mask_text_where_no_secret_is_declared_is_data() -> None:
    document = {"login": {"userName": "***"}, "freeForm": {"passWord": "***"}}
    assert restore_secrets(Vault, document) == document
    assert restore_secrets(Vault, {"words": ["***"]}) == {"words": ["***"]}


def test_shown_value_names_the_keys_and_hides_the_secrets() -> None:
    assert shown_value(STORED["login"], Vault, Path(("login",))) == {
        "user_name": "a",
        "pass_word": "***",
    }
    assert "s-" not in json.dumps(shown_value(STORED, Vault)).replace("s-free", "")


def test_which_models_declare_a_secret() -> None:
    assert declares_secret(Vault)
    assert not declares_secret(FirewallData[Edge])
    assert not declares_secret(QosData[Edge])
    assert declares_secret(DeviceSettingsData)
    assert declares_secret(WanData)
    assert pyvelo.core.HIDDEN == "***"


class Chain(VcoModel):
    """A class that holds itself, and no secret at any depth."""

    name: str = ""
    links: list["Chain"] = Field(default_factory=list)
    by_name: dict[str, "Chain"] = Field(default_factory=dict)


class Branch(VcoModel):
    name: str = ""
    code: str | MISSING = MISSING
    vault: Vault | MISSING = MISSING
    token: Annotated[dict[str, Any] | MISSING, Secret()] = MISSING
    chain: Chain | MISSING = MISSING


class Bank(VcoModel):
    branches: Annotated[list[Branch], KeyField("name", alt="code")] = Field(
        default_factory=list
    )
    spare_branches: list[Branch] = Field(default_factory=list)
    holder: Login | Marked | MISSING = MISSING
    code_as: Annotated[str, Alias("codeAS"), Secret()] = ""


def branch(name: str, code: str) -> dict[str, Any]:
    """A branch each of whose secrets says where it is stored."""
    return {
        "name": name,
        "code": code,
        "vault": {
            "login": login("a", f"{name}-a"),
            "logins": [login("b", f"{name}-b"), login("c", f"{name}-c")],
            "spareLogins": [login("d", f"{name}-d"), login("e", f"{name}-e")],
            "bySite": {"x": login("f", f"{name}-f")},
            "words": [f"{name}-word"],
        },
        "token": {"passWord": f"{name}-token"},
    }


BANK: dict[str, Any] = {
    "branches": [branch("north", "n"), branch("south", "s")],
    "spareBranches": [branch("east", "e"), branch("west", "w")],
    "holder": {"plain": "p", "password": "s-holder"},
    "codeAS": "s-code",
}


def masked_bank() -> dict[str, Any]:
    return cast("dict[str, Any]", hide_secrets(Bank, Path(()), BANK))


def test_a_class_that_holds_itself_declares_no_secret_of_its_own() -> None:
    assert not declares_secret(Chain)
    assert declares_secret(Branch)
    assert declares_secret(Bank)
    assert declares_secret(Login)
    assert not declares_secret(Holder)


def test_every_mask_below_an_item_takes_the_stored_value() -> None:
    document = masked_bank()
    assert "-" not in json.dumps(document)
    assert document["branches"][0]["token"] == "***"
    assert restore_secrets(Bank, document, BANK) == BANK


def test_an_item_without_a_key_is_matched_with_its_secrets_hidden_at_every_depth() -> (
    None
):
    document = masked_bank()
    document["spareBranches"].reverse()
    restored = cast("dict[str, Any]", restore_secrets(Bank, document, BANK))
    assert restored["spareBranches"] == [branch("west", "w"), branch("east", "e")]
    document["spareBranches"][1]["vault"]["bySite"]["x"]["userName"] = "f2"
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Bank, document, BANK)
    assert caught.value.path == "spare_branches[#1].vault.login.pass_word"
    assert "matches no stored item" in caught.value.message


def test_two_items_that_differ_in_a_secret_below_them_are_two_matches() -> None:
    twin = branch("east", "e")
    twin["vault"]["bySite"]["x"]["passWord"] = "another"
    stored = {"spareBranches": [branch("east", "e"), branch("west", "w"), twin]}
    document = cast("dict[str, Any]", hide_secrets(Bank, Path(()), stored))
    spare = document["spareBranches"]
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Bank, {"spareBranches": spare[1:]}, stored)
    assert caught.value.path == "spare_branches[#1].vault.login.pass_word"
    assert "matches 2 stored items" in caught.value.message
    assert "give the secret in full" in caught.value.message
    west = {"spareBranches": [spare[1]]}
    assert restore_secrets(Bank, west, stored) == {
        "spareBranches": [branch("west", "w")]
    }


def test_a_keyed_item_is_restored_below_it_by_the_same_rules() -> None:
    document = masked_bank()
    north, south = document["branches"]
    north["name"] = "north-renamed"
    vault = north["vault"]
    vault["logins"] = [login("new", "given"), *reversed(vault["logins"])]
    vault["spareLogins"].reverse()
    vault["bySite"]["new"] = login("g", "typed")
    document["branches"] = [south, north]
    restored = cast("dict[str, Any]", restore_secrets(Bank, document, BANK))
    assert restored["branches"][0] == branch("south", "s")
    assert restored["branches"][1] == {
        "name": "north-renamed",
        "code": "n",
        "vault": {
            "login": login("a", "north-a"),
            "logins": [
                login("new", "given"),
                login("c", "north-c"),
                login("b", "north-b"),
            ],
            "spareLogins": [login("e", "north-e"), login("d", "north-d")],
            "bySite": {"x": login("f", "north-f"), "new": login("g", "typed")},
            "words": ["north-word"],
        },
        "token": {"passWord": "north-token"},
    }
    vault["spareLogins"][0]["userName"] = "e2"
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Bank, document, BANK)
    assert caught.value.path == "branches[#1].vault.spare_logins[#0].pass_word"
    assert "matches no stored item" in caught.value.message


def test_a_mask_below_a_keyed_item_that_is_new_is_refused_where_it_is() -> None:
    document = masked_bank()
    document["branches"][1]["name"] = "south2"
    document["branches"][1]["code"] = "s2"
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Bank, document, BANK)
    assert caught.value.path == "branches[#1].vault.login.pass_word"
    assert "no such item: the item is new" in caught.value.message
    assert "would be written to the VCO as the secret" in caught.value.message


def test_an_item_given_in_full_needs_no_stored_item() -> None:
    document = {
        "login": login("a", "full"),
        "logins": [login("zz", "full")],
        "spareLogins": [login("q", "full"), login("q", "full")],
        "bySite": {"new": login("g", "full")},
        "words": ["w"],
    }
    assert restore_secrets(Vault, document) == document
    assert restore_secrets(Vault, document, STORED) == document
    assert restore_secrets(Vault, document, {}) == document
    full = {"branches": [branch("new", "x")], "spareBranches": [branch("east", "e")]}
    assert restore_secrets(Bank, full) == full
    assert restore_secrets(Bank, full, BANK) == full


def test_a_secret_that_is_an_object_is_one_mask_and_a_mask_inside_it_is_data() -> None:
    document = {"branches": [{"name": "north", "token": "***"}]}
    assert restore_secrets(Bank, document, BANK) == {
        "branches": [{"name": "north", "token": {"passWord": "north-token"}}]
    }
    inside = {"branches": [{"name": "north", "token": {"passWord": "***"}}]}
    assert restore_secrets(Bank, inside, BANK) == inside
    assert restore_secrets(Bank, inside) == inside


def test_a_place_that_holds_one_class_or_another_has_the_secrets_of_both() -> None:
    assert masked_bank()["holder"] == {"plain": "p", "password": "***"}
    stored = {"holder": {"password": "s-1", "passWord": "s-2", "plain": "***"}}
    document = hide_secrets(Bank, Path(()), stored)
    assert document == {
        "holder": {"password": "***", "passWord": "***", "plain": "***"}
    }
    assert restore_secrets(Bank, document, stored) == stored


def test_a_field_is_read_from_stored_under_any_key_the_class_reads_it_from() -> None:
    by_name = {"login": {"user_name": "a", "pass_word": "s-a"}, "code_as": "s-code"}
    assert restore_secrets(Vault, {"login": login("a", "***")}, by_name) == {
        "login": login("a", "s-a")
    }
    assert restore_secrets(Bank, {"codeAS": "***"}, by_name) == {"codeAS": "s-code"}
    both = {"code_as": "s-name", "codeAS": "s-key"}
    assert restore_secrets(Bank, {"codeAS": "***"}, both) == {"codeAS": "s-key"}
    assert restore_secrets(Bank, {"code_as": "***"}, both) == {"code_as": "s-name"}
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Bank, {"codeAs": "***"}, both)
    assert (caught.value.kind, caught.value.path) == ("masked_secret", "code_as")
    assert "under 2 keys" in caught.value.message
    assert "would be written to the VCO as the secret" in caught.value.message
    assert restore_secrets(Bank, {"codeAs": "***"}, {"code_as": "s"}) == {"codeAs": "s"}


def test_a_value_that_is_no_object_where_a_model_is_declared_is_kept() -> None:
    document = {"login": None, "logins": None, "spareLogins": ["***", 7], "bySite": 0}
    assert restore_secrets(Vault, document, STORED) == document
    assert restore_secrets(Vault, document) == document
    assert hide_secrets(Vault, Path(()), document) == document


def test_a_stored_value_of_another_shape_is_no_value_there() -> None:
    cases: list[tuple[dict[str, Any], dict[str, Any], str]] = [
        ({"login": login("a", "***")}, {"login": [login("a", "s")]}, "login.pass_word"),
        ({"login": login("a", "***")}, {"login": None}, "login.pass_word"),
        (
            {"spareLogins": [login("d", "***")]},
            {"spareLogins": login("d", "s")},
            "spare_logins[#0].pass_word",
        ),
        (
            {"logins": [login("b", "***")]},
            {"logins": {"b": login("b", "s")}},
            "logins[#0].pass_word",
        ),
        (
            {"bySite": {"x": login("f", "***")}},
            {"bySite": [login("f", "s")]},
            "by_site.x.pass_word",
        ),
    ]
    for document, stored, path in cases:
        with pytest.raises(NamedDocumentError) as caught:
            _ = restore_secrets(Vault, document, stored)
        assert (caught.value.kind, caught.value.path) == ("masked_secret", path)
        assert "`stored` has no value there" in caught.value.message


def test_keyed_items_their_stored_keys_do_not_tell_apart_are_matched_by_content() -> (
    None
):
    second = {**login("b", "s-2"), "note": "second"}
    stored = {"logins": [login("b", "s-1"), second, login("c", "s-c")]}
    document = cast("dict[str, Any]", hide_secrets(Vault, Path(()), stored))
    assert restore_secrets(Vault, document, stored) == stored
    document["logins"].reverse()
    assert restore_secrets(Vault, document, stored) == {
        "logins": [login("c", "s-c"), second, login("b", "s-1")]
    }
    document["logins"][0]["note"] = "edited"
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Vault, document, stored)
    assert caught.value.path == "logins[#0].pass_word"
    assert "matches no stored item" in caught.value.message
    twins = {"logins": [login("b", "s-1"), login("b", "s-2")]}
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Vault, hide_secrets(Vault, Path(()), twins), twins)
    assert caught.value.path == "logins[#0].pass_word"
    assert "matches 2 stored items" in caught.value.message


def test_restore_secrets_changes_and_shares_neither_the_document_nor_stored() -> None:
    document, stored = masked(), copy.deepcopy(STORED)
    restored = cast("dict[str, Any]", restore_secrets(Vault, document, stored))
    assert document == masked()
    assert stored == STORED
    restored["words"].append("w3")
    restored["freeForm"]["more"] = 1
    restored["login"]["userName"] = "changed"
    restored["logins"][0]["userName"] = "changed"
    restored["spareLogins"].clear()
    restored["bySite"]["x"]["userName"] = "changed"
    assert document == masked()
    assert stored == STORED
    blob = {"branches": [{"name": "north", "token": "***"}]}
    restored = cast("dict[str, Any]", restore_secrets(Bank, blob, BANK))
    restored["branches"][0]["token"]["passWord"] = "changed"
    assert BANK["branches"][0]["token"] == {"passWord": "north-token"}


@pytest.mark.parametrize(
    ("document", "stored", "what"),
    [
        ({"spareLogins": (login("d", "***"),)}, STORED, "the document holds a tuple"),
        (
            {"login": types.MappingProxyType(login("a", "***"))},
            STORED,
            "the document holds a mappingproxy",
        ),
        ({"freeForm": {"deep": [{1, 2}]}}, STORED, "the document holds a set"),
        (
            {"login": login("a", "***")},
            {"login": types.MappingProxyType(login("a", "s-a"))},
            "`stored` holds a mappingproxy",
        ),
        ({}, {"spareLogins": (login("d", "s-d"),)}, "`stored` holds a tuple"),
        (MISSING, STORED, "the document holds a"),
    ],
)
def test_restore_secrets_refuses_what_json_loads_does_not_give(
    document: object, stored: object, what: str
) -> None:
    with pytest.raises(TypeError) as caught:
        _ = restore_secrets(Vault, document, stored)
    assert what in str(caught.value)
    assert "would be written to the VCO as the secret" in str(caught.value)
    assert "s-" not in str(caught.value)


def test_a_renamed_neighbour_keeps_its_password() -> None:
    second = {
        **neighbor(NEW),
        "neighborIp": "192.0.2.2",
        "id": "00000000-0000-0000-0000-0000000000bb",
    }
    stored = settings(neighbor(OLD), second)
    document = cast(
        "dict[str, Any]", hide_secrets(DeviceSettingsData, Path(()), stored)
    )
    assert OLD not in json.dumps(document)
    assert NEW not in json.dumps(document)
    assert restore_secrets(DeviceSettingsData, document, stored) == stored
    neighbors = document["segments"][0]["bgp"]["neighbors"]
    neighbors.reverse()
    neighbors[1]["neighborIp"] = "192.0.2.9"
    neighbors.append({**neighbor(OTHER), "neighborIp": "192.0.2.3", "id": "cc"})
    restored = cast(
        "dict[str, Any]", restore_secrets(DeviceSettingsData, document, stored)
    )
    assert [
        (item["neighborIp"], item["md5Password"])
        for item in restored["segments"][0]["bgp"]["neighbors"]
    ] == [("192.0.2.2", NEW), ("192.0.2.9", OLD), ("192.0.2.3", OTHER)]


def test_snmp_users_keep_their_passphrases_and_an_edited_one_restates_its_own() -> None:
    users = [{"name": "u1", "passphrase": OLD}, {"name": "u2", "passphrase": NEW}]
    stored = {
        "snmp": {
            "snmpv2c": {"communityList": [OLD, NEW]},
            "snmpv3": {"users": users},
        }
    }
    document = cast(
        "dict[str, Any]", hide_secrets(DeviceSettingsData, Path(()), stored)
    )
    assert document["snmp"]["snmpv2c"] == {"communityList": "***"}
    shown_users = document["snmp"]["snmpv3"]["users"]
    assert shown_users == [
        {"name": "u1", "passphrase": "***"},
        {"name": "u2", "passphrase": "***"},
    ]
    shown_users.reverse()
    restored = cast(
        "dict[str, Any]", restore_secrets(DeviceSettingsData, document, stored)
    )
    assert restored["snmp"]["snmpv2c"] == {"communityList": [OLD, NEW]}
    assert restored["snmp"]["snmpv3"]["users"] == [users[1], users[0]]
    shown_users[0]["privacy"] = True
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(DeviceSettingsData, document, stored)
    assert caught.value.path == "snmp.v3.users[#0].passphrase"
    assert str(caught.value).startswith(
        'snmp.v3.users[#0].passphrase: holds the mask "***", and the stored item'
    )
    assert OLD not in str(caught.value)
    assert NEW not in str(caught.value)


def test_shown_value_without_a_model_or_at_a_secret() -> None:
    assert shown_value(STORED["login"], None) == STORED["login"]
    assert shown_value("s-a", Vault, Path(("login", "passWord"))) == "***"
    assert shown_value(["w1"], Vault, Path(("words",))) == "***"
    assert shown_value(MISSING, Vault, Path(("login",))) is MISSING
    shown_login = shown_value(STORED["logins"], Vault, Path(("logins",)))
    assert shown_login == [
        {"user_name": "b", "pass_word": "***"},
        {"user_name": "c", "pass_word": "***"},
    ]
    assert STORED["logins"][0] == login("b", "s-b")


# --- a value of another shape than its field declares -------------------------


class Shapes(VcoModel):
    """A ``Login`` in each way a field can hold one."""

    one: Login | MISSING = MISSING
    several: list[Login] = Field(default_factory=list)
    keyed: Annotated[list[Login], KeyField("user_name")] = Field(default_factory=list)
    either: Login | list[Login] | MISSING = MISSING
    by_site: dict[str, Login] = Field(default_factory=dict)
    vault: Vault | MISSING = MISSING


SEEN = login("f", "s-f")
HID = login("f", "***")

OTHER_SHAPES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    # A list where a map is declared: an item is an entry of the map, ...
    "a list for a map": ({"bySite": [SEEN]}, {"bySite": [HID]}),
    # ... or a map itself, ...
    "a list of maps for a map": ({"bySite": [{"x": SEEN}]}, {"bySite": [{"x": HID}]}),
    # ... also where a key of that map is spelled as a field, ...
    "a list of maps whose key is a field's": (
        {"bySite": [{"userName": SEEN}]},
        {"bySite": [{"userName": HID}]},
    ),
    # ... at any depth of lists.
    "a list of lists for a map": (
        {"bySite": [[SEEN, {"x": SEEN}], "text", 7, None]},
        {"bySite": [[HID, {"x": HID}], "text", 7, None]},
    ),
    "a list for a map two levels down": (
        {"vault": {"bySite": [SEEN]}},
        {"vault": {"bySite": [HID]}},
    ),
    # One model where a map is declared: a key that holds no object is a field.
    "one model for a map": ({"bySite": SEEN}, {"bySite": HID}),
    "a list under a secret's key of a map": (
        {"bySite": {"passWord": [SEEN]}},
        {"bySite": {"passWord": "***"}},
    ),
    # A map where only a list is declared: an entry is an item.
    "a map for a list": ({"several": {"e0": SEEN}}, {"several": {"e0": HID}}),
    "a map for a keyed list": ({"keyed": {"e0": SEEN}}, {"keyed": {"e0": HID}}),
    "a map for a list, its key a field's": (
        {"several": {"userName": SEEN}},
        {"several": {"userName": HID}},
    ),
}

SAME_SHAPES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    # What the field declares.
    "a map": ({"bySite": {"x": SEEN}}, {"bySite": {"x": HID}}),
    "a map whose key is spelled as a secret": (
        {"bySite": {"passWord": SEEN, "userName": None}},
        {"bySite": {"passWord": HID, "userName": None}},
    ),
    "one model or a list of them": (
        {"either": SEEN, "one": SEEN, "several": [SEEN], "keyed": [SEEN]},
        {"either": HID, "one": HID, "several": [HID], "keyed": [HID]},
    ),
    "a list where one or a list is declared": ({"either": [SEEN]}, {"either": [HID]}),
    # A key the classes do not know, where one model can be: all below is data.
    "a key no class declares": (
        {"one": {"e0": SEEN}, "either": {"e0": SEEN}, "e0": SEEN},
        {"one": {"e0": SEEN}, "either": {"e0": SEEN}, "e0": SEEN},
    ),
    # Another shape, read as it was before.
    "a list for one model": ({"one": [SEEN]}, {"one": [HID]}),
    "one item for a list": ({"several": SEEN}, {"several": HID}),
    "a list under a key of a map": (
        {"bySite": {"x": [SEEN], "userName": [SEEN]}},
        {"bySite": {"x": [HID], "userName": [HID]}},
    ),
    "no object at all": (
        {"bySite": "text", "several": 7, "keyed": None, "one": "***"},
        {"bySite": "text", "several": 7, "keyed": None, "one": "***"},
    ),
    "items that are no objects": (
        {"bySite": {"x": "text", "userName": "f"}, "several": ["***", 7]},
        {"bySite": {"x": "text", "userName": "f"}, "several": ["***", 7]},
    ),
}


@pytest.mark.parametrize("name", list(OTHER_SHAPES))
def test_a_value_of_another_shape_has_its_secrets_hidden(name: str) -> None:
    document, hidden = OTHER_SHAPES[name]
    given = copy.deepcopy(document)
    assert hide_secrets(Shapes, Path(()), document) == hidden
    assert document == given
    assert "s-f" not in json.dumps(shown_value(document, Shapes))


@pytest.mark.parametrize("name", list(SAME_SHAPES))
def test_a_value_of_the_declared_shape_is_hidden_as_declared(name: str) -> None:
    document, hidden = SAME_SHAPES[name]
    assert hide_secrets(Shapes, Path(()), document) == hidden


def places(value: object, path: tuple[str | int, ...] = ()) -> Iterator[Path]:
    """The path of every value in the JSON *value*, itself first."""
    yield Path(path)
    if is_dict(value):
        for key, item in value.items():
            yield from places(item, (*path, cast("str", key)))
    elif isinstance(value, list):
        for position, item in enumerate(cast("list[object]", value)):
            yield from places(item, (*path, position))


def at(value: object, path: Path) -> object:
    for part in path.parts:
        if isinstance(value, str):
            return value  # the mask of all below
        value = cast("Any", value)[part]
    return value


@pytest.mark.parametrize("name", [*OTHER_SHAPES, *SAME_SHAPES])
def test_a_part_of_a_value_is_hidden_as_it_is_in_the_whole(name: str) -> None:
    document, _ = (OTHER_SHAPES | SAME_SHAPES)[name]
    whole = hide_secrets(Shapes, Path(()), document)
    for path in places(document):
        part = hide_secrets(Shapes, path, at(document, path))
        assert part == at(whole, path), path


def test_shown_value_keeps_the_keys_of_another_shape_and_hides_its_secrets() -> None:
    # No class names the keys of a list found where a map is declared.
    assert shown_value([SEEN], Shapes, Path(("by_site",))) == [HID]
    assert shown_value({"e0": SEEN}, Shapes, Path(("several",))) == {"e0": HID}
    assert shown_value(SEEN, Shapes, Path(("by_site",))) == HID
    assert shown_value("s-f", Shapes, Path(("by_site", "pass_word"))) == "***"
    assert shown_value("s-f", Shapes, Path(("by_site", 0, "pass_word"))) == "***"


STORED_SHAPES: dict[str, Any] = {
    "one": login("a", "s-a"),
    "several": [login("d", "s-d")],
    "keyed": [login("b", "s-b")],
    "bySite": {"x": login("f", "s-f"), "passWord": login("g", "s-g")},
}


@pytest.mark.parametrize(
    ("document", "path"),
    [
        ({"bySite": [HID]}, "by_site[#0].pass_word"),
        ({"bySite": [{"x": HID}]}, "by_site[#0].x.pass_word"),
        ({"bySite": [[HID]]}, "by_site[#0][#0].pass_word"),
        ({"bySite": HID}, "by_site.pass_word"),
        ({"bySite": {"x": [HID]}}, "by_site.x[#0].pass_word"),
        ({"several": {"e0": HID}}, "several.e0.pass_word"),
        ({"several": HID}, "several.pass_word"),
        ({"keyed": {"e0": HID}}, "keyed.e0.pass_word"),
    ],
)
def test_a_mask_in_a_value_of_another_shape_is_refused(
    document: dict[str, Any], path: str
) -> None:
    # Nothing stored is at the place of such a mask, whichever way it is read.
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Shapes, document, STORED_SHAPES)
    assert (caught.value.kind, caught.value.path) == ("masked_secret", path)
    assert "`stored` has no value there" in caught.value.message
    assert "would be written to the VCO as the secret" in caught.value.message


@pytest.mark.parametrize(
    ("document", "path"),
    [
        ({"bySite": {"passWord": [HID]}}, "by_site.passWord[#0].pass_word"),
        ({"bySite": [{"passWord": [HID]}]}, "by_site[#0].passWord[#0].pass_word"),
        ({"several": {"passWord": HID}}, "several.passWord.pass_word"),
        ({"keyed": {"passWord": [HID]}}, "keyed.passWord[#0].pass_word"),
    ],
)
def test_a_mask_below_a_key_spelled_as_a_secret_of_another_shape_is_refused(
    document: dict[str, Any], path: str
) -> None:
    # The value under the key is no mask: it holds one, where reading it as a
    # model of the classes there has a secret.
    assert '"***"' in json.dumps(document)
    with pytest.raises(NamedDocumentError) as caught:
        _ = restore_secrets(Shapes, document, STORED_SHAPES)
    assert (caught.value.kind, caught.value.path) == ("masked_secret", path)
    assert "`stored` has no value there" in caught.value.message


@pytest.mark.parametrize("name", [*OTHER_SHAPES, *SAME_SHAPES])
def test_a_value_of_another_shape_with_no_mask_is_restored_as_given(name: str) -> None:
    document, hidden = (OTHER_SHAPES | SAME_SHAPES)[name]
    restored = restore_secrets(Shapes, document, STORED_SHAPES)
    assert restored == document
    assert hide_secrets(Shapes, Path(()), restored) == hidden


def test_a_mask_in_an_entry_of_a_map_takes_the_stored_entrys_value() -> None:
    document = {"bySite": {"passWord": login("g", "***"), "x": login("f", "***")}}
    assert restore_secrets(Shapes, document, STORED_SHAPES) == {
        "bySite": STORED_SHAPES["bySite"]
    }
