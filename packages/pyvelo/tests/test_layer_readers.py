"""Every reader of a field goes through ``field_kind``: a carrier field behaves as a plain field of its inner type.

The save computation, the merge engine, the change list and the walkers read
the type and the markers of a field. On a ``Layered`` model that is the
carrier, not the type, so each reads it through ``field_kind``; and they read
the value of a field through ``unwrap``, which sees the value an ``Owned`` stores.
"""

import copy
import random
import uuid
from collections.abc import Callable
from typing import Annotated, Any, cast

import pytest
from layered_models import Flagged, Mod, Rule, Seg, Settings, mod_json
from pydantic import MISSING, AfterValidator, Field, ValidationError, field_validator
from pydantic.fields import FieldInfo
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration.ref_owners import ref_owners
from pyvelo.client.module_write import wire_dump
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import (
    Additive,
    ByPresence,
    ConfigContext,
    ContextDefault,
    Default,
    Edge,
    EdgeLocal,
    FixedList,
    HubRole,
    Inheritable,
    KeyField,
    Layered,
    NameOf,
    Normalizer,
    Owned,
    OwnsRef,
    PatchError,
    Path,
    Positional,
    Profile,
    Provides,
    RefersTo,
    RefOnly,
    Resolvable,
    Secret,
    ServerOwned,
    Unconverted,
    VcoModel,
    add,
    field_models,
    kind_default,
    list_kind,
)
from pyvelo.core.base import list_item_type
from pyvelo.core.fields import (
    get_context_defaults,
    get_fixed_list,
    get_hub_role,
    get_key_spec,
    get_name_of,
    get_normalizer,
    get_owns_ref,
    get_provides,
    get_ref_only,
    get_refers_to,
    get_resolvable,
    is_edge_local,
    is_positional,
    is_secret,
    is_server_owned,
    ref_only_defaults,
)
from pyvelo.core.identity import SegmentIdentity
from pyvelo.core.layers import L, unwrap
from pyvelo.core.rebase import (
    carry_unset_defaults,
    find_lossy_assignments,
    python_path,
    raw_changes,
    rebase,
)
from pyvelo.core.references import references
from pyvelo.core.secrets import hide_secrets
from pyvelo.core.walk import walk_fields

from ._type_support import SegmentResolverFactory

PROFILE_LEVEL = ConfigContext.PROFILE
EDGE_LEVEL = ConfigContext.EDGE


def _edge(**keys: object) -> Mod[Edge]:
    return Mod[Edge].model_validate(mod_json(**keys))


def _profile(**keys: object) -> Mod[Profile]:
    return Mod[Profile].model_validate(mod_json(**keys))


# ---------------------------------------------------------------------------
# unwrap
# ---------------------------------------------------------------------------


def test_unwrap_gives_what_an_owned_stores_and_anything_else_as_it_is() -> None:
    settings = Settings(a=3)
    assert unwrap(Owned(settings)) is settings
    assert unwrap(Owned(0)) == 0
    assert unwrap(Owned(None)) is None
    assert unwrap(Owned(MISSING)) is MISSING
    assert unwrap(settings) is settings
    assert unwrap(None) is None
    assert unwrap([1]) == [1]


# ---------------------------------------------------------------------------
# The identity of the save computation, on generated documents
# ---------------------------------------------------------------------------

_JUNK = [None, True, False, 0, 1, -3, 2.5, "", "x", "0"]
_BOOLS = [True, False, 1, 0, "true", "false", "yes", "no"]
_INTS = [0, 3, -2, "4", 5.0]


def _junk(rng: random.Random, depth: int = 0) -> object:
    roll = rng.random()
    if depth > 1 or roll < 0.5:
        return rng.choice(_JUNK)
    if roll < 0.75:
        return [_junk(rng, depth + 1) for _ in range(rng.randint(0, 2))]
    return {
        f"k{rng.randint(0, 9)}": _junk(rng, depth + 1) for _ in range(rng.randint(0, 2))
    }


def _with_unknown_keys(rng: random.Random, node: object) -> None:
    """Add unknown keys at every object of a valid document, and below the unknown values too."""
    if isinstance(node, dict):
        for value in list(node.values()):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            _with_unknown_keys(rng, value)
        for _ in range(rng.randint(0, 2)):
            node[f"future{rng.randint(0, 99)}"] = _junk(rng)
    elif isinstance(node, list):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            _with_unknown_keys(rng, item)


def _one_of(rng: random.Random, values: list[Any], *, null: bool) -> object:
    return None if null and rng.random() < 0.25 else rng.choice(values)


def _segment(rng: random.Random, index: int, *, edge: bool) -> dict[str, object]:
    found: dict[str, object] = {
        "segment": {
            "segmentId": index,
            "name": f"seg{index}",
            "type": "REGULAR",
            "segmentLogicalId": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        }
    }
    if rng.random() < 0.7:
        found["rules"] = [
            {"name": f"r{j}", "action": rng.choice(["allow", "deny"])}
            | ({"ruleLogicalId": f"id{j}"} if rng.random() < 0.5 else {})
            for j in range(rng.randint(0, 3))
        ]
    if rng.random() < 0.5:
        found["mapping"] = (
            None
            if edge and rng.random() < 0.2
            else {"override": rng.choice([True, False]), "value": rng.choice(_INTS)}
        )
    if rng.random() < 0.3:
        found["legacy"] = rng.choice([None, 4, "5"])
    return found


def _document(rng: random.Random, *, edge: bool) -> object:
    """A raw document of ``Mod`` for the edge or the profile layer that parses: nulls only where the layer holds them."""
    doc: dict[str, object] = {
        "segments": [_segment(rng, i, edge=edge) for i in range(rng.randint(0, 3))]
    }
    if rng.random() < 0.6:
        doc["enabled"] = _one_of(rng, _BOOLS, null=edge)
    if rng.random() < 0.6:
        doc["level"] = _one_of(rng, _INTS, null=True)
    for key in ("settings", "pairA", "pairB"):
        if rng.random() < 0.4:
            doc[key] = (
                None
                if edge and rng.random() < 0.2
                else {"a": rng.choice(_INTS), "b": rng.choice(_BOOLS)}
            )
    if rng.random() < 0.4:
        doc["pairOn"] = _one_of(rng, _BOOLS, null=edge)
    if rng.random() < 0.4:
        doc["affinity"] = (
            None
            if edge and rng.random() < 0.2
            else {
                "override": rng.choice(_BOOLS),
                "affinity": ["a", "b"][: rng.randint(0, 2)],
            }
        )
    if rng.random() < 0.3:
        doc["legacy"] = [rng.choice(_INTS) for _ in range(rng.randint(0, 3))]
    # A key of the other layer is read by neither parse and kept by the save.
    if rng.random() < 0.3:
        doc["profileNote" if edge else "edgeNote"] = _junk(rng)
    _with_unknown_keys(rng, doc)
    return doc


@pytest.mark.parametrize(
    ("model_type", "level"),
    [(Mod[Edge], EDGE_LEVEL), (Mod[Profile], PROFILE_LEVEL)],
    ids=["edge", "profile"],
)
def test_the_identity_holds_for_generated_layers(
    model_type: type[Mod[Edge]] | type[Mod[Profile]], level: ConfigContext
) -> None:
    # apply(diff(B, B), R) == R for every R, and the whole no-edit path of a save
    # (the carried defaults, the lossy guard) leaves a document as read.
    for seed in range(300):
        rng = random.Random(seed)
        raw = _document(rng, edge=level is EDGE_LEVEL)
        parsed = model_type.model_validate(raw)
        before = wire_dump(parsed, level)
        assert rebase(copy.deepcopy(raw), before, before, model_type) == raw, seed

        current = copy.deepcopy(parsed)
        after = wire_dump(current, level)
        carry_unset_defaults(parsed, current, before, after)
        assert after == before, seed
        assert find_lossy_assignments(parsed, current, before, after, level) == [], seed
        assert rebase(copy.deepcopy(raw), before, after, model_type) == raw, seed


def test_an_edge_edit_reaches_the_payload_and_nothing_else() -> None:
    # carry_unset_defaults treats a field neither model has *set* as a default
    # made anew on each parse; an Owned changed by set_local is not in
    # model_fields_set, and the edit must not be carried back to what was read.
    raw = mod_json(settings={"a": 1, "x": 7}, level=2, futureKey=[1])
    baseline = Mod[Edge].model_validate(raw)
    current = copy.deepcopy(baseline)
    current.level.set_local(9)
    current.settings.localize().a = 5
    before = wire_dump(baseline, EDGE_LEVEL)
    after = wire_dump(current, EDGE_LEVEL)
    carry_unset_defaults(baseline, current, before, after)
    assert find_lossy_assignments(baseline, current, before, after, EDGE_LEVEL) == []
    payload = rebase(copy.deepcopy(raw), before, after, Mod[Edge])
    assert payload == {
        "segments": [],
        "settings": {"a": 5, "x": 7},
        "level": 9,
        "futureKey": [1],
    }


def test_a_lossy_assignment_inside_an_owned_value_is_found() -> None:
    # The stored model is a model like another: assigning the default of a key
    # that was absent in it changes nothing in the dump, and is found.
    baseline = _edge(settings={"a": 1})
    current = copy.deepcopy(baseline)
    current.settings.localize().b = False
    before = wire_dump(baseline, EDGE_LEVEL)
    after = wire_dump(current, EDGE_LEVEL)
    assert before == after
    found = find_lossy_assignments(baseline, current, before, after, EDGE_LEVEL)
    assert [str(path) for path in found] == ["settings.b"]


def test_no_lossy_error_for_an_inheritable() -> None:
    # A profile Inheritable can always express "not stored": assigning it its
    # display default where the raw key is absent is a change, not a loss.
    baseline = _profile()
    current = copy.deepcopy(baseline)
    assert baseline.enabled is True
    current.enabled = True
    current.level = None
    before = wire_dump(baseline, PROFILE_LEVEL)
    after = wire_dump(current, PROFILE_LEVEL)
    assert "enabled" not in before
    assert after["enabled"] is True
    assert after["level"] is None
    assert find_lossy_assignments(baseline, current, before, after, PROFILE_LEVEL) == []
    payload = rebase(mod_json(), before, after, Mod[Profile])
    assert payload == mod_json(enabled=True, level=None)


# ---------------------------------------------------------------------------
# What a field holds, seen through its kind
# ---------------------------------------------------------------------------


def test_list_kind_and_key_spec_see_through_additive() -> None:
    rules = Seg[Edge].model_fields["rules"]
    assert list_kind(rules) == "keyed"
    spec = get_key_spec(rules)
    assert spec is not None
    assert spec.paths == ("name",)
    assert spec.alt == "rule_logical_id"
    assert list_kind(Seg[Profile].model_fields["rules"]) == "keyed"
    assert list_kind(Mod[Edge].model_fields["segments"]) == "keyed"
    # Inheritable and Unconverted fields of other shapes are no lists.
    assert list_kind(Mod[Edge].model_fields["settings"]) is None
    assert list_kind(Mod[Edge].model_fields["legacy"]) == "value"


def test_field_models_and_list_item_type_see_through_kinds() -> None:
    assert field_models(Mod[Edge].model_fields["settings"]) == (Settings,)
    assert field_models(Mod[Profile].model_fields["settings"]) == (Settings,)
    assert field_models(Seg[Edge].model_fields["mapping"]) == (Flagged,)
    assert field_models(Seg[Edge].model_fields["rules"]) == (Rule,)
    assert field_models(Mod[Edge].model_fields["segments"]) == (Seg[Edge],)
    assert field_models(Mod[Edge].model_fields["enabled"]) == ()
    assert field_models(Seg[Edge].model_fields["legacy"]) == ()
    item = list_item_type(Seg[Edge].model_fields["rules"])
    assert item is not None
    assert item.variants == (Rule,)
    assert list_item_type(Mod[Edge].model_fields["settings"]) is None


class _Address(VcoModel):
    address_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    note: str = ""


class _Named(VcoModel):
    name: Annotated[str, NameOf(SegmentIdentity)]
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0


class _Inside(Layered[L]):
    """A ``Resolvable`` and a ``KeyField`` written inside the kinds."""

    target: Annotated[
        Inheritable[Annotated[int, Resolvable(SegmentIdentity, "segment_id")]],
        ByPresence(),
        Default(0),
    ] = Field(default=...)
    address: Annotated[
        Inheritable[_Address], ByPresence(), Default(factory=_Address)
    ] = Field(default=...)
    named: Additive[Annotated[list[_Named], KeyField("name")]] = kind_default(
        factory=list
    )


def test_a_resolvable_inside_a_kind_is_found() -> None:
    for model_type in (_Inside[Edge], _Inside[Profile]):
        target = get_resolvable(model_type.model_fields["target"])
        assert target is not None
        assert target.identity_type is SegmentIdentity
        assert target.id_attr == "segment_id"
        assert get_resolvable(model_type.model_fields["address"]) is None
        (found,) = field_models(model_type.model_fields["address"])
        assert get_resolvable(found.model_fields["address_id"]) is not None


class _Marked(Layered[L]):
    """A marker inside the type argument of a kind, one field for each getter."""

    normalized: Unconverted[Annotated[str, Normalizer(str.lower)]] = kind_default("")
    contextual: Unconverted[Annotated[int, ContextDefault(edge=1, profile=2)]] = (
        kind_default(0)
    )
    fixed: Unconverted[
        Annotated[list[Rule], KeyField("name"), FixedList(editable=("action",))]
    ] = kind_default(factory=list)
    local: Unconverted[Annotated[int, EdgeLocal]] = kind_default(0)
    positional: Unconverted[Annotated[list[Rule], Positional()]] = kind_default(
        factory=list
    )
    named: Unconverted[Annotated[str, NameOf(SegmentIdentity)]] = kind_default("")
    owned_by_the_vco: Unconverted[Annotated[str, ServerOwned()]] = kind_default("")
    secret: Unconverted[Annotated[str, Secret()]] = kind_default("")
    owns: Unconverted[
        Annotated[
            uuid.UUID | None,
            OwnsRef("marked:owner", SegmentIdentity, by="logical_id", row="logicalId"),
        ]
    ] = kind_default(None)
    ref_only: Unconverted[
        Annotated[
            uuid.UUID | None | MISSING,
            RefOnly(default="marked:ref"),
            Resolvable(SegmentIdentity, "logical_id"),
        ]
    ] = kind_default(factory=lambda: MISSING)
    provides: Unconverted[
        Annotated[list[Rule], KeyField("name"), Provides("rule", key="name")]
    ] = kind_default(factory=list)
    refers: Unconverted[Annotated[int, RefersTo("rule")]] = kind_default(0)
    hubs: Unconverted[Annotated[list[Rule], HubRole("hub")]] = kind_default(
        factory=list
    )


@pytest.mark.parametrize(
    ("name", "found"),
    [
        ("normalized", get_normalizer),
        ("contextual", get_context_defaults),
        ("fixed", get_fixed_list),
        ("local", is_edge_local),
        ("positional", is_positional),
        ("named", get_name_of),
        ("owned_by_the_vco", is_server_owned),
        ("secret", is_secret),
        ("owns", get_owns_ref),
        ("ref_only", get_ref_only),
        ("provides", get_provides),
        ("refers", get_refers_to),
        ("hubs", get_hub_role),
    ],
)
def test_every_getter_of_a_marker_finds_one_inside_a_kind(
    name: str, found: Callable[[FieldInfo], object]
) -> None:
    for model_type in (_Marked[Edge], _Marked[Profile]):
        assert found(model_type.model_fields[name])
        # And only for the field that has it.
        others = [other for other in model_type.model_fields if other != name]
        assert not any(found(model_type.model_fields[other]) for other in others)


def test_markers_inside_a_kind_act_as_they_do_outside() -> None:
    assert ref_only_defaults(_Marked[Edge]) == {"ref_only": "marked:ref"}
    assert _Marked[Edge].model_fields["ref_only"].exclude  # a dump leaves it out
    assert get_key_spec(_Marked[Edge].model_fields["fixed"]) is not None
    assert list_kind(_Marked[Edge].model_fields["fixed"]) == "keyed"
    assert list_kind(_Marked[Edge].model_fields["positional"]) == "positional"
    assert list_kind(_Marked[Edge].model_fields["hubs"]) == "undeclared"
    data = _Marked[Edge].model_validate({"secret": "hunter2", "named": "x"})
    shown = hide_secrets(_Marked[Edge], Path(()), wire_dump(data, EDGE_LEVEL))
    assert cast("dict[str, object]", shown)["secret"] == "***"


def test_a_key_field_inside_a_kind_is_found() -> None:
    named = _Inside[Edge].model_fields["named"]
    spec = get_key_spec(named)
    assert spec is not None
    assert spec.paths == ("name",)
    assert list_kind(named) == "keyed"


@pytest.fixture
async def registry(make_segment_resolver: SegmentResolverFactory) -> ResolverRegistry:
    resolver = await make_segment_resolver(
        [
            {
                "name": "Global Segment",
                "segment_id": 0,
                "object_id": 100,
                "segment_logical_id": uuid.UUID(int=1),
            },
            {
                "name": "PCI",
                "segment_id": 7,
                "object_id": 101,
                "segment_logical_id": uuid.UUID(int=2),
            },
        ]
    )
    found = ResolverRegistry()
    found.register(resolver)
    return found


def test_merge_resolves_a_name_inside_a_kind(registry: ResolverRegistry) -> None:
    # As a plain field does: a profile-layer patch names a segment by its name.
    profile = _Inside[Profile].model_validate({})
    _ = profile.merge_patch(
        {"target": "PCI", "address": {"address_id": "Global Segment"}},
        resolvers=registry,
    )
    assert profile.target == 7
    assert profile.address.address_id == 0
    _ = profile.merge_patch({"address": {"address_id": "PCI"}}, resolvers=registry)
    assert profile.address.address_id == 7
    assert "target" in profile.model_fields_set


def test_merge_builds_an_item_by_name_inside_a_kind(registry: ResolverRegistry) -> None:
    # A keyed list inside Additive: the new item's name fills its ID, as it does
    # in a plain list.
    for model_type in (_Inside[Edge], _Inside[Profile]):
        data = model_type.model_validate({})
        _ = data.merge_patch(
            {"named": {"PCI": add({})}},
            resolvers=registry,
        )
        (item,) = data.named
        assert (item.name, item.segment_id) == ("PCI", 7)


class _Limits(VcoModel):
    max_rate: int = 1


class _Item(Layered[L]):
    name: str
    on: Annotated[Inheritable[bool], ByPresence(), Default(False)] = Field(default=...)
    limits: Annotated[Inheritable[_Limits], ByPresence(), Default(factory=_Limits)] = (
        Field(default=...)
    )


class _Items(Layered[L]):
    items: Annotated[list[_Item[L]], KeyField("name")] = Field(default_factory=list)


def test_a_new_item_is_built_with_its_inheritable_fields_on_each_layer() -> None:
    # The built item is checked against the patch that built it: a boolean for
    # a boolean field is no error, and a key the model lacks inside an
    # Inheritable value is refused as it is inside any other.
    for model_type in (_Items[Edge], _Items[Profile]):
        data = model_type.model_validate({})
        _ = data.merge_patch(
            {"items": {"a": add({"on": True, "limits": {"max_rate": 3}})}}
        )
        (item,) = data.items
        assert unwrap(item.on) is True
        limits = unwrap(item.limits)
        assert isinstance(limits, _Limits)
        assert limits.max_rate == 3
        with pytest.raises(PatchError, match="no field 'nope'"):
            _ = data.merge_patch({"items": {"b": add({"limits": {"nope": 1}})}})
        assert len(data.items) == 1


def test_merge_validates_the_inner_type_of_an_inheritable() -> None:
    profile = _profile()
    _ = profile.merge_patch({"settings": {"a": 5}, "level": 3})
    assert profile.settings == Settings(a=5)
    assert profile.level == 3
    assert {"settings", "level"} <= profile.model_fields_set
    assert wire_dump(profile, PROFILE_LEVEL)["settings"] == {"a": 5, "b": False}
    with pytest.raises(PatchError, match="settings.a"):
        _ = profile.merge_patch({"settings": {"a": "x"}})
    with pytest.raises(PatchError, match="invalid value"):
        _ = profile.merge_patch({"level": "x"})
    assert profile.settings.a == 5


def test_a_patch_into_a_display_default_is_stored_even_when_it_equals_it() -> None:
    profile = _profile()
    assert "settings" not in wire_dump(profile, PROFILE_LEVEL)
    _ = profile.merge_patch({"settings": {"a": 1}})
    assert profile.settings == Settings()  # the display default, as it was
    assert wire_dump(profile, PROFILE_LEVEL)["settings"] == {"a": 1, "b": False}


def test_merge_into_the_children_of_an_edge_layer() -> None:
    # Structural and Additive children of any stored layer take a patch.
    for model_type in (Mod[Edge], Mod[Profile]):
        data = model_type.model_validate(
            mod_json(
                segments=[
                    {
                        "segment": {"segmentId": 0, "name": "Global Segment"},
                        "rules": [{"name": "r1"}],
                    }
                ]
            )
        )
        _ = data.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "rules": {"r2": add({"name": "r2", "action": "deny"})},
                        "legacy": 3,
                    }
                },
                "legacy": [1, 2],
            }
        )
        (segment,) = data.segments
        assert [r.name for r in segment.rules] == ["r1", "r2"]
        assert segment.rules[1].action == "deny"
        assert segment.legacy == 3
        assert data.legacy == [1, 2]
        with pytest.raises(PatchError, match="rules"):
            _ = data.merge_patch(
                {"segments": {"Global Segment": {"rules": {"r2": add({"name": "r2"})}}}}
            )


@pytest.mark.parametrize("patch", [{"level": 4}, {"settings": {"a": 5}}])
def test_a_patch_never_stores_a_bare_value_in_place_of_an_owned(
    patch: dict[str, object],
) -> None:
    # Reaching an edge Inheritable by a patch is the next step; until then it is
    # refused loudly and the model is as it was.
    edge = _edge(level=3, settings={"a": 1})
    before = wire_dump(edge, EDGE_LEVEL)
    with pytest.raises(TypeError, match="set_local"):
        _ = edge.merge_patch(patch)
    assert isinstance(edge.level, Owned)
    assert isinstance(edge.settings, Owned)
    assert wire_dump(edge, EDGE_LEVEL) == before


def _not_negative(value: int) -> int:
    if value < 0:
        raise ValueError("level must not be negative")
    return value


class _Guarded(Layered[L]):
    level: Annotated[Inheritable[int], ByPresence(), Default(0)] = Field(default=...)

    @field_validator("level", mode="before")
    @classmethod
    def _not_negative(cls, value: object) -> object:
        if isinstance(value, int) and value < 0:
            raise ValueError("level must not be negative")
        return value


def test_a_field_validator_on_an_inheritable_refuses_alike_through_set_local_and_a_patch() -> (
    None
):
    edge = _Guarded[Edge].model_validate({"level": 1})
    profile = _Guarded[Profile].model_validate({"level": 1})
    with pytest.raises(PatchError) as from_set_local:
        edge.level.set_local(-1)
    with pytest.raises(PatchError) as from_patch:
        _ = profile.merge_patch({"level": -1})
    assert "must not be negative" in str(from_set_local.value)
    assert str(from_set_local.value) == str(from_patch.value)
    assert from_set_local.value.path == from_patch.value.path
    assert from_set_local.value.kind == from_patch.value.kind == "invalid_value"
    assert edge.level.stored == 1
    assert profile.level == 1


def test_the_context_view_sees_the_kind_of_a_field() -> None:
    # A field of one layer is absent at the other level, whatever the layer.
    for model_type in (Mod[Edge], Mod[Profile]):
        at_edge = model_type.context(EDGE_LEVEL)
        at_profile = model_type.context(PROFILE_LEVEL)
        assert at_edge.edge_note.is_present
        assert not at_profile.edge_note.is_present
        assert at_profile.profile_note.is_present
        assert not at_edge.profile_note.is_present
        assert at_edge.settings.is_present
        assert at_profile.settings.field_info is model_type.model_fields["settings"]


@pytest.mark.parametrize("mode", ["after", "wrap", "plain"])
def test_a_validator_that_would_see_an_owned_is_refused_at_class_creation(
    mode: str,
) -> None:
    def check(value: object) -> object:
        return value

    def check_wrapping(value: object, handler: object) -> object:
        return value

    function = check_wrapping if mode == "wrap" else check

    with pytest.raises(TypeError, match=rf"Refused\.level.*{mode} field validator"):

        class Refused(Layered[L]):  # pyright: ignore[reportUnusedClass]
            level: Annotated[Inheritable[int], ByPresence(), Default(0)] = Field(
                default=...
            )
            _check = field_validator("level", mode=mode)(function)  # pyright: ignore[reportArgumentType, reportCallIssue, reportUnknownVariableType]

    # The fix it names, and a before validator, are accepted.
    assert _Guarded[Edge].model_validate({"level": 1}).level.stored == 1


class _AfterInside(Layered[L]):
    level: Annotated[
        Inheritable[Annotated[int, AfterValidator(_not_negative)]],
        ByPresence(),
        Default(0),
    ] = Field(default=...)


def test_an_after_validator_inside_the_kind_refuses_the_same_value_on_every_path() -> (
    None
):
    with pytest.raises(ValidationError, match="must not be negative"):
        _AfterInside[Edge].model_validate({"level": -1})
    with pytest.raises(ValidationError, match="must not be negative"):
        _AfterInside[Profile].model_validate({"level": -1})
    edge = _AfterInside[Edge].model_validate({"level": 1})
    profile = _AfterInside[Profile].model_validate({"level": 1})
    with pytest.raises(PatchError, match="must not be negative") as from_set_local:
        edge.level.set_local(-1)
    with pytest.raises(PatchError, match="must not be negative") as from_patch:
        _ = profile.merge_patch({"level": -1})
    assert str(from_set_local.value) == str(from_patch.value)
    assert edge.level.stored == 1
    assert profile.level == 1


# ---------------------------------------------------------------------------
# The change list
# ---------------------------------------------------------------------------


class _Wired(Layered[L]):
    limits: Annotated[Inheritable[_Limits], ByPresence(), Default(factory=_Limits)] = (
        Field(default=...)
    )


def test_raw_changes_name_python_paths_through_kinds() -> None:
    baseline = _profile(settings={"a": 1})
    current = copy.deepcopy(baseline)
    current.settings.a = 2
    changes = raw_changes(
        wire_dump(baseline, PROFILE_LEVEL),
        wire_dump(current, PROFILE_LEVEL),
        Mod[Profile],
    )
    (change,) = changes
    assert str(change.path) == "settings.a"
    assert str(python_path(change.path, Mod[Profile])) == "settings.a"
    assert (change.before, change.after) == (1, 2)


@pytest.mark.parametrize(
    ("model_type", "level"),
    [(_Wired[Edge], EDGE_LEVEL), (_Wired[Profile], PROFILE_LEVEL)],
    ids=["edge", "profile"],
)
def test_a_wire_key_below_a_kind_is_named_by_its_python_name(
    model_type: type[_Wired[Edge]] | type[_Wired[Profile]], level: ConfigContext
) -> None:
    baseline = model_type.model_validate({"limits": {"maxRate": 1}})
    current = copy.deepcopy(baseline)
    limits = unwrap(current.limits)
    assert isinstance(limits, _Limits)
    limits.max_rate = 5
    (change,) = raw_changes(
        wire_dump(baseline, level), wire_dump(current, level), model_type
    )
    assert str(change.path) == "limits.maxRate"
    assert str(python_path(change.path, model_type)) == "limits.max_rate"


# ---------------------------------------------------------------------------
# The walkers read the value an Owned stores
# ---------------------------------------------------------------------------


class _Hub(VcoModel):
    hub_id: Annotated[int, RefersTo("hub", none=(-1,))] = -1


class _Walked(Layered[L]):
    direct: Annotated[Inheritable[_Hub], ByPresence(), Default(factory=_Hub)] = Field(
        default=...
    )
    scalar: Annotated[
        Inheritable[int], RefersTo("hub", none=(-1,)), ByPresence(), Default(-1)
    ] = Field(default=...)
    owner: Annotated[
        Inheritable[uuid.UUID | None],
        OwnsRef("walked:owner", SegmentIdentity, by="logical_id", row="logicalId"),
        ByPresence(),
        Default(None),
    ] = Field(default=...)


def test_walk_fields_goes_into_the_model_an_owned_stores() -> None:
    data = _Walked[Edge].model_validate({"direct": {"hubId": 4}})
    paths = [str(site.path) for site in walk_fields(data, holds=lambda _cls: True)]
    assert paths == ["direct", "direct.hub_id", "scalar", "owner"]


def test_references_read_through_an_owned() -> None:
    edge = _Walked[Edge].model_validate({"direct": {"hubId": 4}, "scalar": 5})
    found = {str(r.path): r.value for r in references("walked", edge)}
    assert found == {"direct.hub_id": 4, "scalar": 5}
    # Nothing stored, or a stored null, is no reference.
    assert references("walked", _Walked[Edge].model_validate({"scalar": None})) == ()
    assert references("walked", _Walked[Edge].model_validate({})) == ()
    profile = _Walked[Profile].model_validate({"direct": {"hubId": 4}, "scalar": 5})
    assert {str(r.path): r.value for r in references("walked", profile)} == found


def test_ref_owners_read_through_an_owned() -> None:
    logical_id = uuid.UUID(int=9)
    for model_type in (_Walked[Edge], _Walked[Profile]):
        data = model_type.model_validate({"owner": str(logical_id)})
        (found,) = ref_owners(data, None)
        assert str(found.path) == "owner"
        assert found.value == logical_id
        assert ref_owners(model_type.model_validate({}), None) == ()


# ---------------------------------------------------------------------------
# A shallow copy
# ---------------------------------------------------------------------------


def test_a_shallow_copy_that_is_validated_does_not_take_the_owned_values() -> None:
    # merge_patch checks a shallow copy of each model it merges into, by
    # validating it; the Owned values it holds are the original's, and stay so.
    edge = _edge(enabled=True)
    clone = edge.model_copy()
    _ = Mod[Edge].model_validate(clone)
    assert clone.enabled is edge.enabled
    _ = edge.mark_detached()
    with pytest.raises(DetachedObjectError, match="take it again"):
        edge.enabled.set_local(False)


def test_an_inheritable_patched_through_a_sub_model_check_keeps_its_owner() -> None:
    # A patch into a segment of an edge layer checks that segment (a shallow
    # copy); its Owned values still belong to it.
    edge = Mod[Edge].model_validate(
        mod_json(
            segments=[
                {
                    "segment": {"segmentId": 0, "name": "Global Segment"},
                    "mapping": {"value": 1},
                }
            ]
        )
    )
    _ = edge.merge_patch({"segments": {"Global Segment": {"legacy": 3}}})
    (segment,) = edge.segments
    assert segment.mapping.stored == Flagged(value=1)
    segment.mapping.set_local(Flagged(value=2))
    assert segment.mapping.stored == Flagged(override=True, value=2)
    _ = edge.mark_detached()
    with pytest.raises(DetachedObjectError, match="take it again"):
        segment.mapping.set_local(Flagged(value=3))
