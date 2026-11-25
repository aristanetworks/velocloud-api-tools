"""Configuration secrets stay out of the text of errors, plans and messages."""

import copy
import re
import traceback
from collections.abc import Iterator
from typing import Annotated, Any, Literal, get_args

import pytest
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
)
from pyvelo.core import (
    DefaultingModel,
    Key,
    PatchError,
    Path,
    Secret,
    VcoModel,
    hide_secrets,
    is_secret,
    list_kind,
)
from pyvelo.core.merge import (
    _list_spec,  # pyright: ignore[reportPrivateUsage]
    _validate_field,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.core.response_check import PlannedChange
from pyvelo.core.show import shown, shown_together
from pyvelo.testing import ModuleStore, as_stored, make_module_json, store_of

from ._type_support import MockVcoClientFactory, require_present

OLD, NEW, OTHER = "OLD-md5-made-up", "NEW-md5-made-up", "THIRD-md5-made-up"
ROOTS = (FirewallData, QosData, DeviceSettingsData, WanData, ControlPlaneData)


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
            todo.extend(_models_in(fi.annotation))
    return seen


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def neighbor(password: object = OLD) -> dict[str, Any]:
    """A BGP neighbour; the password is its last key, where an error's cut-off text shows it."""
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
):
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
    assert "neighborAS" in text and "Field required" in text
    assert OLD not in text and "input_value" not in text
    # The values are still there for a program that asks for them.
    assert exc.value.errors()[0]["input"]["md5Password"] == OLD


async def test_a_write_whose_answer_does_not_parse_raises_without_its_values(
    mock_vco_client: MockVcoClientFactory,
):
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
    assert "neighborAS" in text and "Field required" in text
    assert OLD not in text and "input_value" not in text


async def test_a_write_whose_row_read_back_does_not_parse_raises_without_its_values(
    mock_vco_client: MockVcoClientFactory,
):
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
    assert "neighborAS" in text and "Field required" in text
    assert OLD not in text and "input_value" not in text
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
    """Neither the cause nor the context of *error* quotes an input value; the context is empty."""
    assert error.__context__ is None
    for linked in (error.__cause__, error.__context__):
        if linked is not None:
            assert isinstance(linked, ValidationError)
            for text in (str(linked), repr(linked)):
                assert "input_value" not in text and OLD not in text


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
):
    with pytest.raises(PatchError) as exc:
        _ = Holder().merge_patch(patch)
    cause = exc.value.__cause__
    assert isinstance(cause, ValidationError)
    for text in (str(cause), repr(cause)):
        assert "input_value" not in text and OLD not in text
    assert_chain_quotes_no_input(exc.value)


@pytest.mark.parametrize("field", ["inner", "defaulted"])
@pytest.mark.parametrize("wrap", [False, True])
def test_a_field_typed_as_a_model_does_not_quote_a_value_that_is_not_one(
    field: str, wrap: bool
):
    value: object = [OLD] if wrap else OLD
    with pytest.raises(PatchError) as exc:
        _ = Holder().merge_patch({field: value})
    cause = exc.value.__cause__
    assert isinstance(cause, ValidationError)
    for text in (str(cause), repr(cause), str(exc.value)):
        assert "input_value" not in text and OLD not in text
    assert cause.errors()[0]["input"] == value
    assert_chain_quotes_no_input(exc.value)


def test_no_field_of_the_models_quotes_a_patch_value_in_the_cause_of_its_error():
    """Give every field the module data classes reach a text that none of them accepts."""
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


def test_every_list_field_still_builds_its_adapters():
    """No list or map field of the models refuses the setting on the adapters built for it."""
    built = 0
    for cls in reachable_classes():
        for fi in cls.model_fields.values():
            if list_kind(fi) is None:
                continue
            spec = _list_spec(fi)
            assert spec is not None
            if spec.item_type is not None:
                try:  # the first call builds the adapter of a union of models
                    _ = spec.item_type.validate({})
                except ValidationError:
                    pass
            built += 1
    assert built > 100


SEGMENT = ("segments", Key("Global Segment"))
NEIGHBOR = Path((*SEGMENT, "bgp", "neighbors", Key("192.0.2.1")))


class Marked(VcoModel):
    plain: str = ""
    password: Annotated[str, Secret()] = ""


def test_is_secret():
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
        "WanNetwork.password",
    }
)
NOT_SECRET = frozenset(
    {
        "BgpFilterActionValue.community_additive",
        "CssIpsecSiteData.psk_type",
        "Md5AuthKey.key_number",
        "SegmentBgp.uplink_community",
        "WanNetwork.isp_key",
    }
)
LOOKS_SECRET = re.compile(
    r"passw|passphrase|secret|psk|community|(^|_)key($|_)|token|(^|_)pin($|_)|credential"
)


def reachable_fields() -> dict[str, bool]:
    """``"Class.field"`` to whether it is marked, for every class the module data classes reach."""
    return {
        f"{cls.__name__}.{name}": is_secret(fi)
        for cls in reachable_classes()
        for name, fi in cls.model_fields.items()
    }


def test_the_secret_fields_of_the_module_data_are_the_recorded_set():
    fields = reachable_fields()
    assert frozenset(name for name, marked in fields.items() if marked) == SECRET_FIELDS
    looks = {name for name in fields if LOOKS_SECRET.search(name.split(".")[1])}
    assert looks - SECRET_FIELDS == NOT_SECRET


def test_the_credentials_of_enterprise_services_are_marked():
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
                        "allowedIps": ["10.0.0.1"],
                    }
                },
                "unknown": {"password": "kept"},
            },
            {
                "snmp": {
                    "snmpv2c": {
                        "community": "***",
                        "communityList": "***",
                        "allowedIps": ["10.0.0.1"],
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
def test_hide_secrets(path: Path, value: object, hidden: object):
    given = copy.deepcopy(value)
    assert hide_secrets(DeviceSettingsData, path, value) == hidden
    assert value == given  # the value given is not changed


def test_hide_secrets_without_a_model_hides_nothing():
    assert hide_secrets(None, Path(("a", "password")), "kept") == "kept"


def rotated() -> DeviceSettingsConfigurationModule:
    """Device settings whose one BGP neighbour gets a new password."""
    module = ds(settings(neighbor(OLD)))
    bgp = require_present(module.data.segments[0].bgp)
    bgp.neighbors[0].md5_password = NEW
    return module


def test_shown_hides_the_secrets_of_the_model_at_the_path():
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


def test_a_plan_shows_no_secret_and_keeps_it_on_the_change():
    plan = plan_module(rotated())
    (change,) = plan.changes
    assert (change.before, change.after) == (OLD, NEW)
    text = plan.render()
    assert OLD not in text and NEW not in text
    assert f'{NEIGHBOR}.md5Password: "***" -> "***"' in text
    assert plan.model is DeviceSettingsData


def test_a_plan_made_from_a_dict_hides_secrets_too():
    module = ds({**settings(), "snmp": {"snmpv2c": {"community": OLD}}})
    plan = plan_module(module, {**settings(), "snmp": {"snmpv2c": {"community": NEW}}})
    assert plan.model is DeviceSettingsData
    text = plan.render()
    assert OLD not in text and NEW not in text
    assert 'snmp.snmpv2c.community: "***" -> "***"' in text


def test_the_repr_of_a_plan_and_of_a_write_hold_no_data():
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


def test_a_write_error_shows_no_secret_and_keeps_it_on_the_change():
    module = rotated()
    stored = as_stored(module, settings(neighbor(OTHER)))
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), stored)
    (lost,) = exc.value.lost_edits
    assert (lost.as_read, lost.intended, lost.stored) == (OLD, NEW, OTHER)
    text = "".join(traceback.format_exception(exc.value))
    assert not any(secret in text for secret in (OLD, NEW, OTHER))
    assert f"{NEIGHBOR}.md5Password" in text and text.count("***") == 3


def test_a_write_error_hides_what_another_writer_stored():
    module = ds(settings(neighbor(OLD)))
    require_present(module.data.segments[0].bgp).asn = "65010"
    stored = as_stored(module, settings(neighbor(OTHER)))
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), stored)
    assert [str(c.path) for c in exc.value.server_changes] == [
        f"{NEIGHBOR}.md5Password"
    ]
    assert OLD not in str(exc.value) and OTHER not in str(exc.value)


def test_a_write_error_does_not_show_a_ref_row():
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
    assert OLD not in line and "(nothing)" in line and "***" in line


def test_an_unknown_outcome_shows_no_secret_of_what_it_sent():
    error = WriteOutcomeUnknownError(
        1,
        "v1",
        "its configuration does not hold the module",
        answer="counted",
        module_name="deviceSettings",
        plan=plan_module(rotated()),
    )
    text = str(error)
    assert OLD not in text and NEW not in text
    assert 'md5Password: read "***", meant "***"' in text


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


def logging_on_500_rules() -> FirewallConfigurationModule:
    module = FirewallConfigurationModule.model_validate(
        make_module_json("firewall", firewall(500), configuration_id=10)
    )
    for item in module.data.segments[0].outbound:
        item.logging_enabled = True
    return module


def entries(text: str, key: str) -> int:
    """How many lines of *text* are the entry of a path that ends in *key*."""
    return sum(1 for line in text.splitlines() if key in line)


def test_the_message_of_a_write_error_is_bounded():
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
    assert entries(text, ".loggingEnabled:") == 20
    assert entries(text, ".match.dport_high:") == 20
    assert "  and 480 more: error.lost_edits has them all" in text.splitlines()
    assert "  and 480 more: error.server_changes has them all" in text.splitlines()
    assert entries(error.render(limit=None), ".loggingEnabled:") == 500
    assert " more: " not in error.render(limit=None)
    assert entries(error.render(limit=1), ".loggingEnabled:") == 1


def test_the_message_of_an_unknown_outcome_is_bounded():
    error = WriteOutcomeUnknownError(
        1,
        "v1",
        "its configuration does not hold the module",
        answer="counted",
        module_name="firewall",
        plan=plan_module(logging_on_500_rules()),
    )
    text = str(error)
    assert entries(text, ".loggingEnabled:") == 20
    assert "  and 480 more: error.plan.changes has them all" in text.splitlines()
    assert entries(error.render(limit=None), ".loggingEnabled:") == 500


def test_hide_secrets_walks_a_tuple_as_it_walks_a_list():
    item = ({"neighborIp": "192.0.2.1", "md5Password": OLD},)
    path = Path((*SEGMENT, "bgp", "neighbors"))
    assert hide_secrets(DeviceSettingsData, path, item) == [
        {"neighborIp": "192.0.2.1", "md5Password": "***"}
    ]
    assert hide_secrets(
        DeviceSettingsData, Path(("snmp", "snmpv2c")), {"communityList": ("a", "b")}
    ) == {"communityList": "***"}
