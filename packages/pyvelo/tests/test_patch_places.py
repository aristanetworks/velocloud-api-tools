"""Where a validation error is: the fields below a map key or a union tag are in their python names."""

from typing import Annotated, Any

import pytest
from pydantic import (
    Discriminator,
    Field,
    Tag,
    ValidatorFunctionWrapHandler,
    model_validator,
)
from pyvelo.core import Alias, PatchError, VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.merge import _check_merged, _python_place  # pyright: ignore[reportPrivateUsage]
from pyvelo.core.rebase import Path


class Entry(VcoModel):
    max_rate: Annotated[int, Alias("MaxRate")] = 0
    peer_address: str = ""


class Table(VcoModel):
    by_name: dict[str, Entry] = Field(default_factory=dict)
    entry: Entry = Field(default_factory=Entry)

    @model_validator(mode="wrap")
    @classmethod
    def _from_a_dump(cls, data: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        """Validates an instance again, from what it dumps."""
        if isinstance(data, cls):
            data = data.model_dump(by_alias=True)
        return handler(data)


class Wired(VcoModel):
    peer_address: str = ""


class Wireless(VcoModel):
    ssid_name: Annotated[str, Alias("SsidName")] = ""


def _kind(value: object) -> str:
    wireless = isinstance(value, Wireless) or (
        isinstance(value, dict) and "SsidName" in value
    )
    return "SsidName" if wireless else "peer_address"


Port = Annotated[
    Annotated[Wired, Tag("peer_address")] | Annotated[Wireless, Tag("SsidName")],
    Discriminator(_kind),
]


class Ports(VcoModel):
    ports: Annotated[list[Port], KeyField("peer_address")] = Field(default_factory=list)
    either: Port | None = None


def test_a_map_key_that_is_a_field_alias_keeps_its_text() -> None:
    place = _python_place(("by_name", "MaxRate", "peerAddress"), (Table,))
    assert place == Path(("by_name", "MaxRate", "peer_address"))
    place = _python_place(("by_name", "max_rate", "MaxRate"), (Table,))
    assert place == Path(("by_name", "max_rate", "max_rate"))


def test_a_union_tag_that_is_a_field_name_keeps_its_text() -> None:
    place = _python_place(("ports", 0, "SsidName", "SsidName"), (Ports,))
    assert place == Path(("ports", 0, "SsidName", "ssid_name"))
    place = _python_place(("either", "peer_address", "peer_address"), (Ports,))
    assert place == Path(("either", "peer_address", "peer_address"))
    # A field that is not a union gives no tag: the next part is a field.
    place = _python_place(("entry", "MaxRate"), (Table,))
    assert place == Path(("entry", "max_rate"))


def test_a_merged_model_that_is_not_valid_names_its_fields_as_a_patch_writes_them() -> (
    None
):
    bad = Entry.model_construct(max_rate=0, peer_address=5)
    table = Table.model_construct(by_name={"MaxRate": bad}, entry=Entry())
    with (
        pytest.warns(UserWarning, match="Pydantic serializer warnings"),
        pytest.raises(PatchError) as exc,
    ):
        _check_merged(table, Table.model_validate, Path(()))
    assert exc.value.message == (
        "Table is not valid after this change: "
        "by_name.MaxRate.peer_address: Input should be a valid string"
    )
