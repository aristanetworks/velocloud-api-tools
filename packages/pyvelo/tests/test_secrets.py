"""Configuration secrets stay out of the text of errors, plans and messages."""

import copy
import traceback
from collections.abc import Iterator
from typing import Annotated, Any, Literal, get_args

import pytest
from pydantic import Discriminator, Field, ValidationError
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import WriteOutcomeUnknownError
from pyvelo.client.models.configuration.control_plane.root import ControlPlaneData
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    GetConfiguration,
)
from pyvelo.core import DefaultingModel, PatchError, Path, VcoModel, list_kind
from pyvelo.core.merge import (
    _list_spec,  # pyright: ignore[reportPrivateUsage]
    _validate_field,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.testing import ModuleStore, make_module_json, store_of

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
