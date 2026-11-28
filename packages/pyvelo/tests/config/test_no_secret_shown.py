"""No output pyvelo shows holds a secret, and every declared secret is hidden.

Every value pyvelo shows goes through ``shown_value``: the plan text, a shown
change, a field change, a write error's lines and ``not_redone()``, the
effective comparison's entries, ``named_dump()``. What is in the VCO's form
(``model_dump()``, ``ModulePlan.data`` and ``.changes``, a write error's
lists and ``redo_patch()``, the requests sent) keeps complete values: the save
and the redo read them.

The first test sweeps the shown outputs of two plans, saves and write errors
for the secrets of its fixtures, checks that each output that shows a value
with a secret shows it masked, and checks that the fixtures' VCO-form outputs
do hold the secrets, so that the sweep is not over a fixture with nothing to
hide. The others are over the declarations: every ``Secret()`` field any
model class reaches, through the fields it declares, is hidden there, also in
a value of another shape than declared; no key of a list is a secret; and
``named_value`` is used only where a value keeps its secrets on purpose.
"""

import ast
import copy
import functools
import json
import traceback
import uuid
from collections.abc import Iterator
from pathlib import Path as FilePath
from typing import Any, Final, NamedTuple

import pytest
import pyvelo
from hub_state import (
    NEIGHBOR_IP,
    bgp_with_a_password,
    hub_pipeline,
    state_with_a_bgp_password,
)
from profile_state import module_store_pipeline, profile_state
from pydantic import MISSING
from pyvelo.client.exceptions import ModuleWriteError, WriteNotAppliedError
from pyvelo.client.module_check import check_module_write
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    ConfigurationBase,
    DeviceSettingsConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.config.effective import EffectiveComparison, compare_module
from pyvelo.config.state import ConfigPlan, ConfigState
from pyvelo.core import (
    HIDDEN,
    Effective,
    Key,
    Path,
    Profile,
    VcoModel,
    effective_view,
    shown_value,
    wire_name,
)
from pyvelo.core.fields import get_key_spec, is_secret
from pyvelo.core.response_check import PlannedChange
from pyvelo.testing import as_stored, make_module_json
from segment_helpers import GLOBAL, ds_entry, firewall_entry, qos_entry
from test_compare_effective import (  # pyright: ignore[reportImplicitRelativeImport]
    SecretData,
)
from tests.test_named_keys import (  # pyright: ignore[reportImplicitRelativeImport]
    Held,
    held_by,
    model_classes,
)
from tests.test_secrets import (  # pyright: ignore[reportImplicitRelativeImport]
    NETWORK_ID,
)

READ, MEANT, KEPT = "s3cret-read", "s3cret-meant", "s3cret-kept"
"""The secret as read, the one an edit sets, and the one another writer stored."""

RAW = {
    "plan.data",
    "plan.changes",
    "error.lost_edits",
    "error.redo_patch",
    "requests",
    "model_dump",
    "result.server_changes",
}
"""What is in the VCO's form and holds complete values.

The save and the redo read it.
"""

PASSWORD_PATCH: Final[dict[str, Any]] = {
    "segments": {
        "Global Segment": {"bgp": {"neighbors": {NEIGHBOR_IP: {"md5_password": MEANT}}}}
    }
}
"""The BGP neighbour of ``state_with_a_bgp_password`` gets ``MEANT``."""


def text(value: object) -> str:
    """*value* as text that holds every string in it, a value of an object too."""
    return json.dumps(value, default=repr)


def module_plan(plan: ConfigPlan, name: str) -> ModulePlan:
    (found,) = [module for module in plan.modules if module.name == name]
    return found


def rows_of(state: ConfigState) -> list[AnyStoredModule]:
    modules = state.profile_config.modules
    assert modules is not MISSING
    return list(modules)


def row_of(state: ConfigState, name: str) -> AnyStoredModule:
    (row,) = [row for row in rows_of(state) if row.name == name]
    return row


def plan_outputs(plans: dict[str, ModulePlan]) -> dict[str, str]:
    """What each module plan, by its name here, shows.

    Its text, its repr, and each of its changes as it shows them.
    """
    shown: dict[str, str] = {}
    for name, plan in plans.items():
        shown[f"{name}.render"] = plan.render()
        shown[f"repr({name})"] = repr(plan)
        shown[f"{name}.shown_change"] = repr(
            [plan.shown_change(change) for change in plan.changes]
        )
    return shown


def error_outputs(name: str, error: ModuleWriteError) -> dict[str, str]:
    """What the write error *error*, under *name*, shows."""
    return {
        f"str({name})": str(error),
        f"{name}.render": error.render(limit=None),
        f"repr({name})": repr(error),
        f"{name} traceback": "".join(traceback.format_exception(error)),
        f"{name}.not_redone": repr(error.not_redone()),
    }


def wan_with_an_isp_key() -> WanConfigurationModule:
    """A WAN module whose network has ``READ`` as its ISP key, then ``MEANT``."""
    network = {
        "internalId": NETWORK_ID,
        "logicalId": "00:00:00:00:00:02:0000",
        "name": "LTE",
        "interface": "CELL1",
        "type": "WIRELESS",
        "addressingVersion": "IPv4",
        "ispKey": READ,
    }
    raw = {"links": [], "networks": [network]}
    module = WanConfigurationModule.model_validate(make_module_json("WAN", raw))
    networks = module.data.networks
    assert networks is not MISSING
    networks[0].isp_key = MEANT
    return module


def write_error(state: ConfigState, plan: ModulePlan) -> ModuleWriteError:
    """The error of a write of *plan*, after which the VCO holds ``KEPT``.

    As the neighbour's password, which the plan sets: a lost edit. And as the
    password of a neighbour only the VCO has: a server change.
    """
    row = row_of(state, plan.name)
    stored: dict[str, Any] = copy.deepcopy(plan.data)
    (segment,) = [s for s in stored["segments"] if "bgp" in s]
    neighbors = segment["bgp"]["neighbors"]
    neighbors[0]["md5Password"] = KEPT
    other = {"neighborIp": "192.0.2.9", "id": "00000000-0000-0000-0000-0000000000bb"}
    neighbors.append(neighbors[0] | other)
    with pytest.raises(ModuleWriteError) as caught:
        _ = check_module_write(row, plan, as_stored(row, stored))
    assert KEPT in text(caught.value.server_changes)
    return caught.value


ROW_ID: Final = uuid.UUID("00000000-0000-0000-0000-000000000005")
AUTH_ROW: Final[dict[str, Any]] = {
    "id": 101,
    "enterpriseObjectId": 5,
    "configurationId": 100,
    "moduleId": 1,
    "logicalId": str(ROW_ID),
    "ref": "deviceSettings:authentication",
    "data": {"sharedSecret": READ},
}
"""A ref row of a RADIUS server, whose data holds its shared secret."""


def with_a_radius_row() -> DeviceSettingsConfigurationModule:
    """A device settings module whose refs hold ``AUTH_ROW``."""
    raw = {"lan": {"networks": [], "interfaces": []}, "segments": []}
    refs = {"deviceSettings:authentication": copy.deepcopy(AUTH_ROW)}
    json_row = make_module_json("deviceSettings", raw, refs=refs)
    return DeviceSettingsConfigurationModule.model_validate(json_row)


def ref_row_error(module: DeviceSettingsConfigurationModule) -> ModuleWriteError:
    """A write of *module* that lost a ref row it added, with ``KEPT`` in its data."""
    row = {**AUTH_ROW, "data": {"sharedSecret": KEPT}}
    path = Path(("deviceSettings:authentication", Key((str(ROW_ID), 5))))
    lost = PlannedChange(path, MISSING, row, MISSING, part="refs")
    return WriteNotAppliedError(
        1,
        "v1",
        module_name="deviceSettings",
        plan=plan_module(module),
        module=module,
        lost_edits=[lost],
    )


SECOND_IP: Final = "192.0.2.2"

GUEST_PATCH: Final[dict[str, Any]] = {
    "segments": {
        "Global Segment": {
            "bgp": {"neighbors": {NEIGHBOR_IP: {"md5_password": MEANT}}}
        },
        "Guest": {
            "_op": "add",
            "bgp": {
                "enabled": True,
                "asn": "65000",
                "neighbors": [
                    {
                        "neighbor_ip": "192.0.2.7",
                        "neighbor_as": "65001",
                        "enable_md5": True,
                        "md5_password": MEANT,
                    }
                ],
            },
        },
    }
}
"""The first neighbour gets ``MEANT``, and Guest is added with a neighbour with it."""


def two_neighbours() -> dict[str, Any]:
    """The Global Segment of the device settings: two BGP neighbours with ``READ``."""
    bgp = bgp_with_a_password(READ)
    neighbors = bgp["neighbors"]
    second = {"neighborIp": SECOND_IP, "id": "00000000-0000-0000-0000-0000000000ab"}
    neighbors.append(neighbors[0] | second)
    return {**ds_entry(GLOBAL), "bgp": bgp}


def keep_the_second_password(data: dict[str, Any]) -> None:
    """In the device settings *data*, ``KEPT`` as the password of ``SECOND_IP``."""
    for segment in data["segments"]:
        for neighbor in segment.get("bgp", {}).get("neighbors", []):
            if neighbor["neighborIp"] == SECOND_IP:
                neighbor["md5Password"] = KEPT


def second_password_error(state: ConfigState, plan: ModulePlan) -> ModuleWriteError:
    """The error of a write of *plan* after which the VCO holds ``KEPT``.

    As the first neighbour's password, which the plan sets: a lost edit. And
    as the second's, which the plan sends as read: a server change at the
    password itself.
    """
    row = row_of(state, plan.name)
    stored: dict[str, Any] = copy.deepcopy(plan.data)
    keep_the_second_password(stored)
    (segment,) = [s for s in stored["segments"] if s["segment"]["segmentId"] == 0]
    segment["bgp"]["neighbors"][0]["md5Password"] = KEPT
    with pytest.raises(ModuleWriteError) as caught:
        _ = check_module_write(row, plan, as_stored(row, stored))
    at_the_secret = [
        change
        for change in caught.value.server_changes
        if change.path.parts[-1] == "md5Password"
    ]
    assert [(c.as_read, c.stored) for c in at_the_secret] == [(READ, KEPT)]
    return caught.value


async def two_neighbours_outputs() -> tuple[dict[str, str], dict[str, str]]:
    """The shown outputs and the VCO-form ones of a second plan, save and error.

    A profile whose Global Segment has two BGP neighbours with ``READ`` sets
    the first to ``MEANT`` and adds Guest with a neighbour with ``MEANT``. A
    write after which the VCO holds ``KEPT`` at both neighbours raises; then
    the state is saved through a store that keeps ``KEPT`` as the second's,
    which the save reports as a server change.
    """
    state = await profile_state(
        [two_neighbours()], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )
    pipeline, store = module_store_pipeline(state)
    _ = await pipeline.apply(state, module="device_settings", patch=GUEST_PATCH)
    plan = state.plan()
    ds = module_plan(plan, "deviceSettings")
    error = second_password_error(state, ds)
    (added,) = [
        c
        for c in state.get_field_changes("device_settings")
        if str(c.path) == "segments[Guest]"
    ]
    assert '"md5_password": "***"' in text(added.after)
    shown = {
        "Guest plan.render": plan.render(),
        "Guest field changes": repr(state.get_field_changes()),
        "Guest plan.warnings": repr(plan.warnings),
        **plan_outputs(
            {f"Guest {module.name} plan": module for module in plan.modules}
        ),
        **error_outputs("server error", error),
    }

    def keep_it(new: dict[str, Any], _: object) -> None:
        if new["name"] == "deviceSettings":
            keep_the_second_password(new["data"])

    store.on_store.append(keep_it)
    result = await pipeline.save(state)
    shown |= {
        "repr(Guest result)": repr(result),
        "Guest result.warnings": repr(result.warnings),
    }
    raw = {
        "plan.data": text(ds.data),
        "plan.changes": text(ds.changes),
        "error.lost_edits": text(error.lost_edits),
        "error.redo_patch": text(error.redo_patch(include_clashes=True)),
        "requests": text(store.requests),
        "model_dump": text([row.model_dump() for row in rows_of(state)]),
        "result.server_changes": text(result.server_changes),
    }
    return shown, raw


async def device_settings_outputs() -> tuple[dict[str, str], dict[str, str]]:
    """The shown outputs and the VCO-form ones of a plan, a save and a write error.

    A profile whose device settings hold a BGP neighbour whose password is
    ``READ`` sets it to ``MEANT``; a write of the device settings after which
    the VCO holds ``KEPT`` raises; then the state is saved through the mock
    store. Beside it, a WAN network's ISP key goes from ``READ`` to ``MEANT``,
    and a ref row with ``READ`` in its data is read, and lost with ``KEPT``.
    """
    state = await state_with_a_bgp_password(READ)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    _ = await pipeline.apply(state, module="device_settings", patch=PASSWORD_PATCH)
    plan = state.plan()
    ds = module_plan(plan, "deviceSettings")
    wan, radius = wan_with_an_isp_key(), with_a_radius_row()
    rows = [*rows_of(state), wan, radius]
    error, ref_error = write_error(state, ds), ref_row_error(radius)
    shown = {
        "plan.render": plan.render(),
        "repr(plan)": repr(plan),
        "field changes": repr(state.get_field_changes()),
        "plan.warnings": repr(plan.warnings),
        "named_dump(configuration)": text(state.profile_config.named_dump()),
        "named_dump(rows)": text([row.named_dump() for row in rows]),
        **plan_outputs(
            {f"{module.name} plan": module for module in plan.modules}
            | {"WAN plan": plan_module(wan), "RADIUS plan": plan_module(radius)}
        ),
        **error_outputs("error", error),
        **error_outputs("ref error", ref_error),
    }
    since = len(store.requests)
    result = await pipeline.save(state)
    shown |= {"repr(result)": repr(result), "result.warnings": repr(result.warnings)}
    plans = [ds, plan_module(wan)]
    raw = {
        "plan.data": text([p.data for p in plans]),
        "plan.changes": text([p.changes for p in plans]),
        "error.lost_edits": text([error.lost_edits, ref_error.lost_edits]),
        "error.redo_patch": text(error.redo_patch(include_clashes=True)),
        "requests": text(store.requests[since:]),
        "model_dump": text([row.model_dump() for row in rows]),
        "result.server_changes": text(result.server_changes),
    }
    return shown, raw


def effective_comparison_outputs() -> dict[str, str]:
    """An effective comparison whose entries hold objects with a secret.

    The VCO lacks the profile's ``login``, has an item more in ``logins``, and
    a key no class reads (``login.user``) with an object in it.
    """
    profile = SecretData[Profile].model_validate(
        {
            "login": {"user": "u", "password": READ},
            "logins": [{"user": "v", "password": MEANT}],
        }
    )
    view = effective_view(None, profile, SecretData[Effective])
    server: dict[str, object] = {
        "logins": [
            {"user": "v", "password": KEPT},
            {"user": "w", "password": KEPT},
        ],
        "login.user": {"password": KEPT},
    }
    entries = compare_module("secret", view, server, ignores=())
    assert len(entries) >= 3
    return {
        "comparison.render": EffectiveComparison(tuple(entries)).render(),
        "comparison.entries": repr(entries),
    }


WITHOUT_A_SECRET: Final = ("QOS plan", "firewall plan", "RADIUS plan")
"""The module plans that show no secret.

QoS and firewall hold none here, and the RADIUS row is sent as read, so its
plan names no row.
"""


def shows_a_secret(name: str) -> bool:
    """Whether the shown output *name* holds a value with a secret, masked.

    All do but a repr of a plan, a write error or a save, which counts the
    entries; the warnings, which are empty in these fixtures (they are swept
    as they are); and the plans of ``WITHOUT_A_SECRET``.
    """
    return not (
        name.startswith("repr(")
        or name.endswith("warnings")
        or any(plan in name for plan in WITHOUT_A_SECRET)
    )


async def test_no_shown_output_of_a_plan_a_save_or_a_write_error_holds_a_secret() -> (
    None
):
    shown, raw = await device_settings_outputs()
    shown |= effective_comparison_outputs()
    guest_shown, guest_raw = await two_neighbours_outputs()
    shown |= guest_shown
    leaks = [
        name
        for name, shown_text in shown.items()
        if any(secret in shown_text for secret in (READ, MEANT, KEPT))
    ]
    assert leaks == []
    # Each output that shows a value with a secret shows it masked: none of
    # them went empty, or stopped reaching the secret.
    unmasked = [
        name
        for name, shown_text in shown.items()
        if shows_a_secret(name) and HIDDEN not in shown_text
    ]
    assert unmasked == []
    # The fixture does hold them: what is in the VCO's form carries each.
    assert MEANT in raw["plan.data"]
    assert READ in raw["plan.changes"]
    assert MEANT in raw["plan.changes"]
    assert KEPT in raw["error.lost_edits"]
    assert MEANT in raw["error.redo_patch"]
    assert MEANT in raw["requests"]
    assert READ in raw["model_dump"]
    assert MEANT in raw["model_dump"]
    assert set(raw) == RAW
    # And the second: the VCO kept another password than the one sent, which
    # the save reports and the error lists.
    assert KEPT in guest_raw["result.server_changes"]
    assert KEPT in guest_raw["error.lost_edits"]
    assert MEANT in guest_raw["requests"]
    assert set(guest_raw) == RAW


# --- the declarations -------------------------------------------------------

SECRET: Final = "s3cret"


@functools.cache
def held(cls: type[VcoModel]) -> tuple[Held, ...]:
    """What the fields of *cls* hold (``held_by``), but a configuration's rows.

    A configuration dumps each row of ``modules`` by the row's own class
    (``ConfigurationBase.named_dump``), and each row class is a root here.
    """
    if issubclass(cls, ConfigurationBase):
        return tuple(h for h in held_by(cls) if h.name != "modules")
    return tuple(held_by(cls))


def has_a_secret(cls: type[VcoModel]) -> bool:
    return any(is_secret(field_info) for field_info in cls.model_fields.values())


@functools.cache
def secret_holders() -> frozenset[type[VcoModel]]:
    """Every class that has a ``Secret()`` field, or holds one that does, at any depth.

    By the fields each class declares (``held``), to a fixed point, so that a
    class that holds itself is answered right.
    """
    universe: set[type[VcoModel]] = set()
    left = list(model_classes())
    while left:
        cls = left.pop()
        if cls not in universe:
            universe.add(cls)
            left.extend(h.cls for h in held(cls))
    holding = {cls for cls in universe if has_a_secret(cls)}
    grown = True
    while grown:
        found = {
            cls
            for cls in universe - holding
            if any(h.cls in holding for h in held(cls))
        }
        holding |= found
        grown = bool(found)
    return frozenset(holding)


def roots() -> tuple[type[VcoModel], ...]:
    return model_classes()


def nested(parts: tuple[str | int, ...], value: object) -> object:
    """*value* at *parts* below a field: in an item of a list, an entry of a map."""
    for part in reversed(parts):
        value = [value] * (part + 1) if isinstance(part, int) else {part: value}
    return value


class Step(NamedTuple):
    """A key on the way to a secret, and where the next class is below it."""

    key: str
    """The key, as the VCO spells it."""
    parts: tuple[str | int, ...]
    """Where the next class is below the key's value (``Held.parts``)."""
    only_a_list: bool
    """Whether a list of that class is all the field declares."""


def only_a_list(cls: type[VcoModel], name: str) -> bool:
    """Whether the field *name* of *cls* holds a class in the items of a list only."""
    return all(h.parts[:1] == (0,) for h in held(cls) if h.name == name)


def secret_trails(
    root: type[VcoModel], inside: tuple[type[VcoModel], ...] = ()
) -> Iterator[tuple[Step, ...]]:
    """For each ``Secret()`` field *root* reaches, the keys on the way to it.

    The last is the secret's own. A class is entered once per path (*inside*).
    """
    for name, field_info in root.model_fields.items():
        if is_secret(field_info):
            yield (Step(wire_name(root, name), (), only_a_list=False),)
    for place in held(root):
        if place.cls in (*inside, root) or place.cls not in secret_holders():
            continue
        step = Step(
            wire_name(root, place.name), place.parts, only_a_list(root, place.name)
        )
        for trail in secret_trails(place.cls, (*inside, root)):
            yield (step, *trail)


def document_of(trail: tuple[Step, ...]) -> object:
    """The smallest document that holds ``SECRET`` at the end of *trail*.

    An object per model, a one-item list per list, a one-entry map per map,
    and so a list in an entry where a class declares a map of lists.
    """
    value: object = SECRET
    for step in reversed(trail):
        value = {step.key: nested(step.parts, value)}
    return value


def other_shapes(trail: tuple[Step, ...]) -> Iterator[tuple[Step, ...]]:
    """*trail* with one value in another shape than its field declares.

    Each of the shapes whose secrets the walk hides: an entry of a map given
    as the item of a list (a list where a map is declared); an item of a
    list given as an entry of a map, where a list is all the field declares
    (a map where a list is); and a map of the class that has the secret given
    as one object of it, where the key of that object on the way holds the
    secret or a list (one object where a map is: a key there that holds an
    object is an entry of the map, as declared).
    """

    def with_parts(position: int, parts: tuple[str | int, ...]) -> tuple[Step, ...]:
        step = trail[position]._replace(parts=parts)
        return (*trail[:position], step, *trail[position + 1 :])

    for position, step in enumerate(trail):
        for index, part in enumerate(step.parts):
            if isinstance(part, str):
                parts = (*step.parts[:index], 0, *step.parts[index + 1 :])
                yield with_parts(position, parts)
        if step.only_a_list and step.parts == (0,):
            yield with_parts(position, ("e0",))
        holds_no_object = position == len(trail) - 2 or (
            position + 1 < len(trail) and trail[position + 1].parts[:1] == (0,)
        )
        if step.parts == ("k",) and holds_no_object:
            yield with_parts(position, ())


def documents_with_a_secret(root: type[VcoModel]) -> Iterator[object]:
    """For each ``Secret()`` field *root* reaches, the smallest document with it.

    In the VCO's keys, as the classes declare it (``document_of``), then in
    each other shape the walk reads (``other_shapes``).
    """
    for trail in secret_trails(root):
        yield document_of(trail)
        yield from map(document_of, other_shapes(trail))


def test_every_declared_secret_is_hidden_wherever_the_classes_reach_it() -> None:
    reached = 0
    for root in roots():
        for document in documents_with_a_secret(root):
            assert SECRET not in json.dumps(shown_value(document, root)), (
                root,
                document,
            )
            reached += 1
    # 1725 documents from 90 roots, 697 of them of the declared shape and
    # 1028 of another: a fall is a secret the walk no longer reaches.
    assert reached >= 1720


def secret_keys() -> list[str]:
    """Each key path, alt key and label of a keyed list that names a secret.

    A path is of python names from an item of the list (``KeySpec``); it
    names a secret where any field on the way is marked ``Secret()``.
    """
    found: list[str] = []
    for cls in model_classes():
        for name, field_info in cls.model_fields.items():
            spec = get_key_spec(field_info)
            if spec is None:
                continue
            items = {h.cls for h in held(cls) if h.name == name}
            found.extend(
                f"{cls.__name__}.{name}: {path}"
                for path in (*spec.paths, spec.alt, spec.label)
                if path is not None and names_a_secret(items, path)
            )
    return found


def names_a_secret(classes: set[type[VcoModel]], path: str) -> bool:
    for part in path.split("."):
        below: set[type[VcoModel]] = set()
        for cls in classes:
            field_info = cls.model_fields.get(part)
            if field_info is None:
                continue
            if is_secret(field_info):
                return True
            below |= {h.cls for h in held(cls) if h.name == part and not h.parts}
        classes = below
    return False


def test_no_key_alt_key_or_label_of_a_list_is_a_secret() -> None:
    assert secret_keys() == []


SOURCE: Final = FilePath(pyvelo.__file__).parent


def calls_of(name: str) -> dict[str, set[str]]:
    """Each source file that calls *name*, with the functions the calls are in.

    A file by its path below the package; a call at module level is in ``""``.
    """
    found: dict[str, set[str]] = {}
    for file in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for function, node in _nodes(tree, ""):
            called = node.func if isinstance(node, ast.Call) else None
            if (isinstance(called, ast.Name) and called.id == name) or (
                isinstance(called, ast.Attribute) and called.attr == name
            ):
                found.setdefault(file.relative_to(SOURCE).as_posix(), set()).add(
                    function
                )
    return found


def _nodes(node: ast.AST, function: str) -> Iterator[tuple[str, ast.AST]]:
    """Each node below *node*, with the name of the innermost function it is in."""
    for child in ast.iter_child_nodes(node):
        inner = (
            child.name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            else function
        )
        yield inner, child
        yield from _nodes(child, inner)


def callers_of(name: str) -> set[str]:
    return set(calls_of(name))


def references_of(name: str) -> dict[str, set[str]]:
    """Each source file but ``core/names.py`` that names *name*, with where.

    A use of the name of any kind (a call, a value passed, mapped or wrapped,
    an attribute of a module) is in the function it is in (``""`` at module
    level). An import of it is ``"import"``, and ``"import as <name>"`` where
    it is bound to another name.
    """
    found: dict[str, set[str]] = {}
    for file in sorted(SOURCE.rglob("*.py")):
        where = file.relative_to(SOURCE).as_posix()
        if where == "core/names.py":
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for function, node in _nodes(tree, ""):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                found.setdefault(where, set()).update(
                    "import"
                    if alias.asname in (None, name)
                    else f"import as {alias.asname}"
                    for alias in node.names
                    if alias.name.rsplit(".", 1)[-1] == name
                )
            elif (isinstance(node, ast.Name) and node.id == name) or (
                isinstance(node, ast.Attribute) and node.attr == name
            ):
                found.setdefault(where, set()).add(function)
    return {where: uses for where, uses in found.items() if uses}


def test_a_value_is_shown_through_shown_value_only() -> None:
    # A patch to send back holds the values it writes: the redo patch's
    # bodies are named in full in ``client/exceptions.py``.
    assert callers_of("named_value") == {"core/names.py", "client/exceptions.py"}
    assert calls_of("named_value") == {
        "core/names.py": {"shown_value"},
        "client/exceptions.py": {"_named_change"},
    }
    # Nor is it reached another way: under another name, or passed as a value.
    # ``pyvelo.core`` re-exports it.
    assert references_of("named_value") == {
        "core/__init__.py": {"import"},
        "client/exceptions.py": {"import", "_named_change"},
    }
