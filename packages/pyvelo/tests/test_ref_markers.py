"""``OwnsRef`` and ``RefOnly``.

The markers of the data fields that a ref row goes with.
"""

import copy
import json
import uuid
from typing import Annotated, ClassVar, Literal
from uuid import UUID

import pytest
from pydantic import MISSING, ConfigDict, Field
from pyvelo.client.enums.enterprise import EnterpriseServiceType
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration.device_settings.segment import (
    CssProviderRef,
    DeviceSegment,
    DnsPrimaryProvider,
    SegmentAuthentication,
)
from pyvelo.core import (
    ConfigContext,
    NetworkServiceIdentity,
    OwnsRef,
    RefOnly,
    SegmentIdentity,
    VcoModel,
    get_owns_ref,
    get_ref_only,
)
from pyvelo.core.base import (
    _REF_FILLING_HOOKS,  # pyright: ignore[reportPrivateUsage]
    ref_only_fields,
)
from pyvelo.core.fields import service_type_of
from pyvelo.core.identity import SERVICE_TYPES
from pyvelo.core.rebase import carry_unset_defaults, find_lossy_assignments

DNS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DNS_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class Stub(VcoModel):
    ref: Literal["deviceSettings:authentication", "other"] | MISSING = MISSING
    note: str = ""
    service: Annotated[
        uuid.UUID | None | MISSING, RefOnly(default="deviceSettings:authentication")
    ] = MISSING


class NoDefaultStub(VcoModel):
    ref: str | MISSING = MISSING
    service: Annotated[uuid.UUID | None | MISSING, RefOnly()] = MISSING


class RefLess(VcoModel):
    service: Annotated[
        uuid.UUID | None | MISSING, RefOnly(default="deviceSettings:authentication")
    ] = MISSING


class RequiredStub(VcoModel):
    """A model that cannot be built from defaults: a patch is validated whole."""

    name: str
    ref: str | MISSING = MISSING
    service: Annotated[
        uuid.UUID | None | MISSING, RefOnly(default="deviceSettings:authentication")
    ] = MISSING


class Holder(VcoModel):
    stub: Stub | MISSING = MISSING
    required: RequiredStub | MISSING = MISSING
    stubs: list[Stub] = Field(default_factory=list)


class SubStub(Stub):
    extra_note: str = ""


class Plain(VcoModel):
    note: str = ""


class KeepsExtras(VcoModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")
    ref: str | MISSING = MISSING
    service: Annotated[uuid.UUID | None | MISSING, RefOnly()] = MISSING


def dump(model: VcoModel) -> dict[str, object]:
    return model.model_dump(mode="json", by_alias=True)


def test_a_ref_only_field_is_not_dumped() -> None:
    stub = Stub(service=UUID(DNS_A))
    assert stub.service == UUID(DNS_A)
    assert "service" not in stub.model_dump(by_alias=True)
    assert "service" not in stub.model_dump()
    assert "service" not in dump(stub)


def test_a_ref_only_field_is_not_in_a_compact_dump() -> None:
    stub = Stub(note="n")
    stub.service = UUID(DNS_A)
    compact = stub.model_dump(context={"compact": True}, by_alias=True)
    assert compact == {"ref": "deviceSettings:authentication", "note": "n"}


def test_a_ref_only_field_is_not_in_a_dump_at_a_config_level() -> None:
    stub = Stub()
    stub.service = UUID(DNS_A)
    context = {"config_level": ConfigContext.EDGE}
    assert "service" not in stub.model_dump(by_alias=True, context=context)


def test_a_ref_only_key_in_the_input_is_not_read() -> None:
    stub = Stub.model_validate({"ref": "other", "service": DNS_A})
    assert stub.service is MISSING
    assert "service" not in (stub.model_extra or {})
    assert "service" not in stub.model_fields_set
    assert stub.ref == "other"


def test_a_ref_only_key_is_not_kept_as_an_extra_by_a_model_that_keeps_extras() -> None:
    kept = KeepsExtras.model_validate({"service": DNS_A, "other": 1})
    assert kept.service is MISSING
    assert kept.model_extra == {"other": 1}


def test_a_ref_only_key_of_any_type_is_not_read() -> None:
    # Not even an invalid value is an error: the key is not read.
    assert Stub.model_validate({"service": "not a uuid"}).service is MISSING
    assert Stub.model_validate({"service": None}).service is MISSING


def test_merge_patch_sets_a_ref_only_field() -> None:
    stub = Stub().merge_patch({"service": DNS_A})
    assert stub.service == UUID(DNS_A)


def test_merge_patch_null_clears_a_ref_only_field_to_none_and_leaves_the_ref() -> None:
    stub = Stub(ref="other", service=UUID(DNS_A))
    _ = stub.merge_patch({"service": None})
    assert stub.service is None  # no row, not "read without refs"
    assert stub.ref == "other"


def test_merge_patch_null_on_a_new_ref_only_field_is_none_and_sets_no_ref() -> None:
    stub = Stub().merge_patch({"service": None})
    assert stub.service is None
    assert stub.ref is MISSING


def test_a_constructor_keyword_sets_a_ref_only_field_and_fills_the_ref() -> None:
    stub = Stub(service=UUID(DNS_A))
    assert stub.service == UUID(DNS_A)
    assert stub.ref == "deviceSettings:authentication"
    assert "service" in stub.model_fields_set


def test_a_constructor_keyword_keeps_a_ref_that_is_given() -> None:
    stub = Stub(ref="other", service=UUID(DNS_A), note="n")
    assert (stub.ref, stub.service, stub.note) == ("other", UUID(DNS_A), "n")


def test_a_constructor_keyword_of_the_wire_name_sets_it_too() -> None:

    class Renamed(VcoModel):
        ref: str | MISSING = MISSING
        owner_service: Annotated[uuid.UUID | None | MISSING, RefOnly(default="x")] = (
            MISSING
        )

    by_wire_name: dict[str, object] = {"ownerService": UUID(DNS_A)}
    assert Renamed(**by_wire_name).owner_service == UUID(DNS_A)  # pyright: ignore[reportArgumentType]
    assert Renamed(owner_service=UUID(DNS_A)).ref == "x"


def test_a_constructor_keyword_of_none_is_no_row() -> None:
    stub = Stub(service=None)
    assert stub.service is None
    assert stub.ref is MISSING


def test_a_subclass_constructor_takes_the_keyword() -> None:
    assert SubStub(service=UUID(DNS_A)).ref == "deviceSettings:authentication"


def test_validated_input_does_not_read_a_ref_only_key() -> None:
    assert Stub.model_validate({"service": DNS_A}).service is MISSING


def test_a_parent_validated_from_data_does_not_read_a_ref_only_key() -> None:
    holder = Holder.model_validate(
        {"stub": {"service": DNS_A}, "stubs": [{"service": DNS_B}]}
    )
    assert isinstance(holder.stub, Stub)
    assert holder.stub.service is MISSING
    assert holder.stub.ref is MISSING
    assert holder.stubs[0].service is MISSING


def test_setting_a_value_fills_a_missing_ref_with_the_default() -> None:
    stub = Stub()
    stub.service = UUID(DNS_A)
    assert stub.ref == "deviceSettings:authentication"
    assert "ref" in stub.model_fields_set


def test_setting_a_value_keeps_a_ref_that_is_there() -> None:
    stub = Stub(ref="other")
    stub.service = UUID(DNS_A)
    assert stub.ref == "other"


def test_clearing_to_none_does_not_set_the_ref() -> None:
    stub = Stub()
    stub.service = None
    assert stub.ref is MISSING


def test_setting_missing_does_not_set_the_ref() -> None:
    stub = Stub()
    stub.service = MISSING
    assert stub.ref is MISSING


def test_a_ref_only_field_with_no_default_leaves_the_ref() -> None:
    stub = NoDefaultStub()
    stub.service = UUID(DNS_A)
    assert stub.ref is MISSING
    assert NoDefaultStub().merge_patch({"service": DNS_A}).ref is MISSING


def test_a_model_with_no_ref_field_has_nothing_to_fill() -> None:
    model = RefLess()
    model.service = UUID(DNS_A)
    assert model.service == UUID(DNS_A)
    assert RefLess().merge_patch({"service": DNS_A}).service == UUID(DNS_A)


def test_merge_patch_fills_the_ref_too() -> None:
    stub = Stub().merge_patch({"service": DNS_A})
    assert stub.ref == "deviceSettings:authentication"


def test_merge_patch_keeps_a_ref_that_is_there() -> None:
    stub = Stub(ref="other").merge_patch({"service": DNS_A})
    assert stub.ref == "other"


def test_merge_patch_fills_the_ref_of_a_ref_in_the_same_patch() -> None:
    stub = Stub().merge_patch({"service": DNS_A, "ref": "other"})
    assert stub.ref == "other"


def test_merge_patch_fills_the_ref_of_a_new_sub_model() -> None:
    holder = Holder().merge_patch({"stub": {"service": DNS_A}})
    assert isinstance(holder.stub, Stub)
    assert holder.stub.service == UUID(DNS_A)
    assert holder.stub.ref == "deviceSettings:authentication"


def test_merge_patch_fills_the_ref_of_a_new_sub_model_that_needs_its_fields() -> None:
    holder = Holder().merge_patch({"required": {"name": "n", "service": DNS_A}})
    assert isinstance(holder.required, RequiredStub)
    assert holder.required.service == UUID(DNS_A)
    assert holder.required.ref == "deviceSettings:authentication"


def test_merge_patch_fills_the_ref_of_an_existing_sub_model() -> None:
    holder = Holder(stub=Stub()).merge_patch({"stub": {"service": DNS_A}})
    assert isinstance(holder.stub, Stub)
    assert holder.stub.ref == "deviceSettings:authentication"


def test_a_subclass_of_a_model_with_a_ref_only_field_fills_the_ref_too() -> None:
    sub = SubStub()
    sub.service = UUID(DNS_A)
    assert sub.ref == "deviceSettings:authentication"
    assert SubStub().merge_patch({"service": DNS_A}).ref == (
        "deviceSettings:authentication"
    )
    assert "service" not in dump(sub)


def test_a_ref_only_field_survives_a_copy_and_a_deep_copy_is_assignable() -> None:
    stub = Stub()
    stub.service = UUID(DNS_A)
    assert copy.deepcopy(stub).service == UUID(DNS_A)
    assert stub.model_copy().service == UUID(DNS_A)


def test_a_detached_model_still_refuses_a_ref_only_assignment() -> None:
    stub = Stub()
    _ = stub.mark_detached()
    with pytest.raises(DetachedObjectError):
        stub.service = UUID(DNS_A)
    assert stub.ref is MISSING


def test_object_setattr_sets_a_ref_only_field_without_the_hook() -> None:
    stub = Stub()
    object.__setattr__(stub, "service", UUID(DNS_A))
    assert stub.service == UUID(DNS_A)
    assert stub.ref is MISSING
    assert "service" not in stub.model_fields_set
    assert "ref" not in stub.model_fields_set


def test_ref_only_fields_are_known_per_class() -> None:
    assert dict(ref_only_fields(Stub)) == {"service": "deviceSettings:authentication"}
    assert dict(ref_only_fields(SubStub)) == dict(ref_only_fields(Stub))
    assert dict(ref_only_fields(NoDefaultStub)) == {"service": None}
    assert dict(ref_only_fields(Plain)) == {}


def test_a_class_with_no_ref_only_field_keeps_the_plain_hooks() -> None:
    assert Plain.__setattr__ is VcoModel.__setattr__
    assert Plain.__init__ not in _REF_FILLING_HOOKS
    assert Plain.model_post_init not in _REF_FILLING_HOOKS
    assert Stub.__init__ in _REF_FILLING_HOOKS
    assert Stub.__setattr__ in _REF_FILLING_HOOKS
    assert Stub.model_post_init in _REF_FILLING_HOOKS


def test_the_markers_are_found_on_the_field() -> None:
    assert get_ref_only(Stub.model_fields["service"]) == RefOnly(
        default="deviceSettings:authentication"
    )
    assert get_ref_only(Stub.model_fields["note"]) is None
    assert get_owns_ref(Stub.model_fields["service"]) is None


class WithOwnsRef(VcoModel):
    logical_id: Annotated[
        uuid.UUID | Literal[""],
        OwnsRef(
            "deviceSettings:css:provider",
            NetworkServiceIdentity,
            by="logical_id",
            row="logicalId",
            per_segment=True,
        ),
    ] = ""


def test_owns_ref_keeps_its_declaration() -> None:
    owns = get_owns_ref(WithOwnsRef.model_fields["logical_id"])
    assert owns == OwnsRef(
        "deviceSettings:css:provider",
        NetworkServiceIdentity,
        by="logical_id",
        row="logicalId",
        per_segment=True,
    )
    assert owns is not None
    assert owns.value is None
    assert owns.level is None
    assert get_ref_only(WithOwnsRef.model_fields["logical_id"]) is None


def test_owns_ref_does_not_change_how_the_field_validates_or_dumps() -> None:
    model = WithOwnsRef.model_validate({"logicalId": DNS_A})
    assert model.logical_id == UUID(DNS_A)
    assert dump(model) == {"logicalId": DNS_A}


def test_owns_ref_takes_a_path_and_a_level() -> None:
    owns = OwnsRef(
        "deviceSettings:segment",
        SegmentIdentity,
        by="segment_id",
        value="segment_id",
        row="data.segmentId",
        level=ConfigContext.PROFILE,
    )
    assert owns.value == "segment_id"
    assert owns.level is ConfigContext.PROFILE
    assert owns.per_segment is False


def test_owns_ref_checks_by_against_the_identity() -> None:
    with pytest.raises(TypeError, match="no field 'nope'"):
        OwnsRef("x", SegmentIdentity, by="nope", row="logicalId")


def test_owns_ref_by_is_checked_when_the_class_is_created() -> None:
    with pytest.raises(TypeError, match="no field 'nope'"):

        class Bad(VcoModel):  # pyright: ignore[reportUnusedClass]
            value: Annotated[
                int, OwnsRef("x", SegmentIdentity, by="nope", row="logicalId")
            ] = 0


def test_a_ref_only_assignment_is_not_lossy() -> None:
    baseline = Stub.model_validate({"ref": "other", "note": "n"})
    current = Stub.model_validate({"ref": "other", "note": "n"})
    current.service = UUID(DNS_A)
    before, after = dump(baseline), dump(current)
    carry_unset_defaults(baseline, current, before, after)
    assert find_lossy_assignments(baseline, current, before, after) == []


def test_a_ref_only_assignment_is_not_lossy_inside_a_list() -> None:
    baseline = Holder.model_validate({"stubs": [{"note": "n"}]})
    current = Holder.model_validate({"stubs": [{"note": "n"}]})
    current.stubs[0].service = UUID(DNS_B)
    before, after = dump(baseline), dump(current)
    carry_unset_defaults(baseline, current, before, after)
    assert find_lossy_assignments(baseline, current, before, after) == []


def test_another_lost_assignment_is_still_reported_beside_a_ref_only_one() -> None:
    baseline = Stub.model_validate({})
    current = Stub.model_validate({})
    current.service = UUID(DNS_A)
    current.note = ""  # the default of an absent key
    before, after = dump(baseline), dump(current)
    carry_unset_defaults(baseline, current, before, after)
    assert find_lossy_assignments(baseline, current, before, after) == ["note"]


def test_a_subclass_validated_from_data_does_not_read_a_ref_only_key() -> None:
    sub = SubStub.model_validate({"service": DNS_A})
    assert sub.service is MISSING
    assert sub.ref is MISSING
    assert "service" not in sub.model_fields_set
    assert SubStub.model_validate_json(f'{{"service": "{DNS_A}"}}').service is MISSING


def test_a_subclass_inside_a_parent_validated_from_data_does_not_read_the_key() -> None:
    class SubHolder(VcoModel):
        sub: SubStub | MISSING = MISSING
        subs: list[SubStub] = Field(default_factory=list)

    data = {"sub": {"service": DNS_A}, "subs": [{"service": DNS_A}]}
    for holder in (
        SubHolder.model_validate(data),
        SubHolder.model_validate_json(json.dumps(data)),
    ):
        assert isinstance(holder.sub, SubStub)
        assert holder.sub.service is MISSING
        assert holder.sub.ref is MISSING
        assert holder.subs[0].service is MISSING
        assert holder.subs[0].ref is MISSING


def test_validated_json_does_not_read_a_ref_only_key() -> None:
    assert Stub.model_validate_json(f'{{"service": "{DNS_A}"}}').service is MISSING


def test_the_constructor_hook_does_not_make_pydantic_validate_through_init() -> None:
    assert not Stub.__pydantic_custom_init__
    assert not SubStub.__pydantic_custom_init__


def test_service_type_of_says_what_a_provider_field_takes() -> None:
    assert service_type_of(CssProviderRef, "logical_id") == "cloudSecurityService"
    assert service_type_of(DnsPrimaryProvider, "service") == "dns"
    assert service_type_of(SegmentAuthentication, "service") == "authentication"
    assert service_type_of(CssProviderRef(), "logical_id") == "cloudSecurityService"
    assert service_type_of(CssProviderRef, "ref") is None
    assert service_type_of(DeviceSegment, "segment") is None
    assert set(SERVICE_TYPES.values()) <= {t.value for t in EnterpriseServiceType}
