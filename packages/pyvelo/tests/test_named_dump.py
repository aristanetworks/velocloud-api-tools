"""The named pair: ``named_dump`` is what a caller sees, ``named_validate`` reads it.

``model_dump`` and ``model_validate`` are the VCO's form. ``named_dump`` has
each declared key by attribute name and each secret as the mask, and
``named_validate`` reads such a document back: ``vco_document`` gives it the
VCO's keys and the stored secrets before anything is validated. So a module
row built from a named document keeps a snapshot (``raw_data``, ``raw_refs``)
in the VCO's keys with the real secrets, which is what a save sends.

Here: the pair on a model, and on a device settings row that holds secrets.
``config/test_named_rows.py`` takes the rows of whole states both ways.
"""

import copy
import json
import traceback
import types
import uuid
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.core import NamedDocumentError, VcoModel
from pyvelo.core.base import is_dict, is_list

from .test_named_keys import model_classes
from .test_secrets import OLD, STORED, Login, Vault, ds, login, neighbor, settings


def wire_of(model: VcoModel) -> dict[str, Any]:
    """The wire dump of *model*, as its JSON text reads back."""
    return json.loads(json.dumps(model.model_dump(mode="json", by_alias=True)))


def named_of(model: VcoModel) -> dict[str, Any]:
    """The named dump of *model*, as its JSON text reads back."""
    return json.loads(json.dumps(model.named_dump()))


def test_a_named_dump_is_in_attribute_names_with_every_secret_masked() -> None:
    vault = Vault.model_validate(STORED)
    named = vault.named_dump()
    assert named["login"] == {"user_name": "a", "pass_word": "***"}
    assert named["words"] == "***"
    assert named["by_site"] == {"x": {"user_name": "f", "pass_word": "***"}}
    assert "s-" not in json.dumps(named).replace("s-free", "")


def test_the_document_of_a_named_dump_is_the_wire_dump() -> None:
    vault = Vault.model_validate(STORED)
    wire = vault.model_dump(mode="json", by_alias=True)
    assert json.dumps(
        Vault.vco_document(vault.named_dump(), stored=vault)
    ) == json.dumps(wire)
    again = Vault.named_validate(vault.named_dump(), stored=vault)
    assert again.model_dump(mode="json", by_alias=True) == wire


def test_a_named_dump_without_its_model_is_refused_at_the_first_mask() -> None:
    vault = Vault.model_validate(STORED)
    with pytest.raises(NamedDocumentError, match="no `stored` was given") as caught:
        _ = Vault.named_validate(vault.named_dump())
    assert caught.value.kind == "masked_secret"
    assert caught.value.path == "login.pass_word"


def test_an_edit_of_a_named_dump_is_validated() -> None:
    vault = Vault.model_validate(STORED)
    named = named_of(vault)
    named["login"]["user_name"] = "renamed"
    again = Vault.named_validate(named, stored=vault)
    assert again.login == Login(user_name="renamed", pass_word="s-a")  # noqa: S106 (test data, not a credential)


def test_a_secret_given_in_full_needs_no_stored_model() -> None:
    named = {"login": {"user_name": "a", "pass_word": "typed"}}
    again = Vault.named_validate(named)
    assert again.model_dump(mode="json")["login"] == login("a", "typed")


def test_an_invalid_value_is_refused_in_attribute_names_and_quotes_nothing() -> None:
    vault = Vault.model_validate(STORED)
    named = named_of(vault)
    named["login"]["user_name"] = ["not", "text"]
    with pytest.raises(NamedDocumentError) as caught:
        _ = Vault.named_validate(named, stored=vault)
    error = caught.value
    assert error.kind == "invalid_value"
    assert "login.user_name" in error.message
    assert "userName" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "s-a" not in "".join(traceback.format_exception(error))


def test_an_invalid_value_says_what_it_stops_and_what_to_do() -> None:
    named = {"login": {"user_name": 1}, "logins": "none"}
    with pytest.raises(NamedDocumentError) as caught:
        _ = Vault.named_validate(named)
    assert caught.value.path == ""
    assert str(caught.value) == (
        "this document is not a valid Vault: "
        "login.user_name: Input should be a valid string; "
        "logins: Input should be a valid list. "
        "No model is made of it: a value the model does not take would be kept "
        "as given, and a save would send it to the VCO. Each place is named by "
        "its attribute names, as the document has them; correct the value there "
        "and read the document again."
    )


def test_a_document_in_the_vcos_keys_is_refused_by_named_validate() -> None:
    vault = Vault.model_validate(STORED)
    with pytest.raises(NamedDocumentError) as caught:
        _ = Vault.named_validate(wire_of(vault), stored=vault)
    assert caught.value.kind == "vco_key"
    assert caught.value.path == "login.user_name"


def test_the_document_and_the_stored_model_are_not_changed() -> None:
    vault = Vault.model_validate(STORED)
    named = named_of(vault)
    before = copy.deepcopy(named)
    again = Vault.named_validate(named, stored=vault)
    assert named == before
    assert vault == Vault.model_validate(STORED)
    again.logins[0].pass_word = "changed"
    assert vault.logins[0].pass_word == "s-b"


def test_a_document_in_other_containers_is_read_as_the_json_it_is() -> None:
    # A mapping that is no dict and a tuple are walked by neither ``vco_keys``
    # nor ``restore_secrets``: left as they are, their attribute names and
    # their masks would reach the model, and a row's snapshot, as given.
    vault = Vault.model_validate(STORED)
    named = named_of(vault)
    frozen = types.MappingProxyType(
        {
            **named,
            "login": types.MappingProxyType(named["login"]),
            "logins": tuple(named["logins"]),
            "spare_logins": tuple(
                types.MappingProxyType(item) for item in named["spare_logins"]
            ),
        }
    )
    document: dict[str, Any] = Vault.vco_document(frozen, stored=vault)
    assert json.dumps(document) == json.dumps(wire_of(vault))
    assert type(document) is dict
    assert type(document["login"]) is dict
    assert type(document["logins"]) is list
    assert all(type(item) is dict for item in document["spareLogins"])
    assert Vault.named_validate(frozen, stored=vault) == vault


@pytest.mark.parametrize(
    ("value", "place", "what"),
    [
        (uuid.UUID(int=1), "login.user_name", "a value of type UUID"),
        ({"a", "b"}, "login.user_name", "a value of type set"),
        (Login(user_name="a"), "login", "a value of type Login"),
        (MISSING, "login.user_name", "a value of type sentinel"),
        ({1: "one"}, "free_form", "a key of type int"),
    ],
)
def test_a_value_that_is_not_json_is_refused_where_it_is(
    value: object, place: str, what: str
) -> None:
    vault = Vault.model_validate(STORED)
    named: dict[str, Any] = named_of(vault)
    *above, last = place.split(".")
    holder = named
    for name in above:
        holder = holder[name]
    holder[last] = value
    with pytest.raises(TypeError) as caught:
        _ = Vault.named_validate(named, stored=vault)
    text = str(caught.value)
    assert text.startswith(f"{place}: a named document is JSON")
    assert f", and this holds {what}. " in text
    assert "json.loads(json.dumps(...))" in text
    assert "s-" not in text


def test_a_value_that_is_not_json_is_refused_inside_a_list() -> None:
    named = {"spare_logins": [{"user_name": "a"}, {"user_name": {1, 2}}]}
    with pytest.raises(TypeError, match=r"^spare_logins\[#1\]\.user_name: "):
        _ = Vault.vco_document(named)


def test_no_model_has_a_field_named_as_a_method_of_the_named_pair() -> None:
    # The three are attributes of every model: a field of the name would hide
    # the method, or be hidden by it.
    methods = {"named_dump", "named_validate", "vco_document"}
    classes = model_classes()
    assert len(classes) > 900
    for cls in classes:
        assert not methods & set(cls.model_fields), cls
        assert all(callable(getattr(cls, name)) for name in methods), cls


def test_a_secret_of_a_row_is_masked_and_restored() -> None:
    row = ds(settings(neighbor(OLD)))
    named = row.named_dump()
    assert OLD not in json.dumps(named)
    again = type(row).named_validate(named, stored=row)
    assert OLD in json.dumps(again.raw_data)
    assert "***" not in json.dumps(again.raw_data)


def u(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012x}"


def subinterface(n: int) -> dict[str, Any]:
    """A subinterface whose OSPF passphrase is ``S-sub-n``."""
    return {
        "subinterfaceId": n,
        "subinterfaceType": "SUB_INTERFACE",
        "segmentId": 0,
        "ospf": {"authentication": True, "authId": n, "authPassphrase": f"S-sub-{n}"},
    }


def css_site(n: int) -> dict[str, Any]:
    """An IPsec site of a cloud security service whose key is ``S-psk-n``."""
    return {
        "logicalId": u(n),
        "data": {
            "ikeId": f"edge{n}@example.test",
            "ikeIdType": "FQDN",
            "psk": f"S-psk-{n}",
            "pskType": "alpha",
        },
    }


SECRETS = {
    "S-c1", "S-c2", "S-k1", "S-k2", "S-md5", "S-pppoe",
    "S-psk-1", "S-psk-2", "S-sub-1", "S-sub-2", "S-u1", "S-u2",
}  # fmt: skip
"""Each secret of ``lists_of_secrets``."""


def lists_of_secrets() -> DeviceSettingsConfigurationModule:
    """Device settings with a secret in each list that has no key, and in others.

    The lists without a key: the SNMP v3 users, the keys of the NTP server,
    the subinterfaces of a routed interface and the sites of a segment's cloud
    security service. An item of such a list gets its secret back by its
    content, so the document read back must hold each item exactly as dumped.
    """
    data = settings({**neighbor("S-md5")})
    data["segments"][0]["css"] = {
        "enabled": True,
        "override": True,
        "sites": [css_site(1), css_site(2)],
    }
    data["snmp"] = {
        "snmpv2c": {"communityList": ["S-c1", "S-c2"]},
        "snmpv3": {
            "users": [
                {"name": "u1", "passphrase": "S-u1"},
                {"name": "u2", "passphrase": "S-u2"},
            ]
        },
    }
    data["ntpServer"] = {
        "enabled": True,
        "authentication": "MD5",
        "keys": [{"keyNumber": 1, "key": "S-k1"}, {"keyNumber": 2, "key": "S-k2"}],
    }
    data["routedInterfaces"] = [
        {
            "name": "GE3",
            "segmentId": 0,
            "addressing": {"type": "PPPOE", "username": "u", "password": "S-pppoe"},
            "subinterfaces": [subinterface(1), subinterface(2)],
        }
    ]
    return ds(data)


def secrets_in(value: object) -> set[str]:
    """Each text of *value* that is one of ``SECRETS``."""
    if is_dict(value):
        return set().union(*(secrets_in(item) for item in value.values()))
    if is_list(value):
        return set().union(*(secrets_in(item) for item in value))
    return {value} & SECRETS if isinstance(value, str) else set()


def test_every_secret_of_a_list_without_a_key_comes_back_unedited() -> None:
    row = lists_of_secrets()
    wire = wire_of(row)
    assert secrets_in(wire) == SECRETS
    named = named_of(row)
    assert "S-" not in json.dumps(named)
    data = named["data"]
    assert data["snmp"]["v3"]["users"][1]["passphrase"] == "***"
    assert data["ntp_server"]["keys"][1] == {"key_number": 2, "key": "***"}
    subinterfaces = data["routed_interfaces"][0]["subinterfaces"]
    assert subinterfaces[1]["ospf"]["authentication_passphrase"] == "***"
    assert data["segments"][0]["css"]["sites"][1]["data"]["psk"] == "***"
    assert json.dumps(type(row).vco_document(named, stored=row)) == json.dumps(wire)
    again = type(row).named_validate(named, stored=row)
    assert json.dumps(again.raw_data) == json.dumps(wire["data"])
    assert secrets_in(again.raw_data) == SECRETS
    assert "***" not in json.dumps(again.raw_data)


def test_each_item_of_a_list_without_a_key_keeps_its_secret_when_reordered() -> None:
    row = lists_of_secrets()
    named = named_of(row)
    data = named["data"]
    data["snmp"]["v3"]["users"].reverse()
    data["ntp_server"]["keys"].reverse()
    data["routed_interfaces"][0]["subinterfaces"].reverse()
    data["segments"][0]["css"]["sites"].reverse()
    again = type(row).named_validate(named, stored=row)
    restored = again.model_dump(mode="json", by_alias=True)["data"]
    assert [
        (user["name"], user["passphrase"])
        for user in restored["snmp"]["snmpv3"]["users"]
    ] == [("u2", "S-u2"), ("u1", "S-u1")]
    assert restored["ntpServer"]["keys"] == [
        {"keyNumber": 2, "key": "S-k2"},
        {"keyNumber": 1, "key": "S-k1"},
    ]
    assert [
        (item["subinterfaceId"], item["ospf"]["authPassphrase"])
        for item in restored["routedInterfaces"][0]["subinterfaces"]
    ] == [(2, "S-sub-2"), (1, "S-sub-1")]
    assert [
        site["data"]["psk"] for site in restored["segments"][0]["css"]["sites"]
    ] == ["S-psk-2", "S-psk-1"]
