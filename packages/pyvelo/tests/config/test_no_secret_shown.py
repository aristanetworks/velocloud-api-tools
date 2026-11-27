"""No output pyvelo shows holds a secret, and every declared secret is hidden.

Every value pyvelo shows goes through ``shown_value``: the plan text, a shown
change, a field change, a write error's lines and ``not_redone()``, the
effective comparison's entries, ``named_dump()``. What is in the VCO's form
(``model_dump()``, ``ModulePlan.data`` and ``.changes``, a write error's
lists and ``redo_patch()``, the requests sent) keeps complete values: the save
and the redo read them.

The first test sweeps the shown outputs of one plan, save and write error for
the secrets of its fixture, and checks that the fixture's VCO-form outputs do
hold them, so that the sweep is not over a fixture with nothing to hide. The
others are over the declarations: every ``Secret()`` field any model class
reaches, through the fields it declares, is hidden there; no key of a list is
a secret; and ``named_value`` is called only where a value keeps its secrets
on purpose.
"""

import ast
import copy
import functools
import json
import traceback
import uuid
from collections.abc import Iterator
from pathlib import Path as FilePath
from typing import Any, Final

import pytest
import pyvelo
from hub_state import NEIGHBOR_IP, hub_pipeline, state_with_a_bgp_password
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


async def test_no_shown_output_of_a_plan_a_save_or_a_write_error_holds_a_secret() -> (
    None
):
    shown, raw = await device_settings_outputs()
    shown |= effective_comparison_outputs()
    leaks = [
        name
        for name, shown_text in shown.items()
        if any(secret in shown_text for secret in (READ, MEANT, KEPT))
    ]
    assert leaks == []
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


def documents_with_a_secret(
    root: type[VcoModel], inside: tuple[type[VcoModel], ...] = ()
) -> Iterator[object]:
    """For each ``Secret()`` field *root* reaches, the smallest document with it.

    The document holds ``SECRET`` at that field, in the VCO's keys: an object
    per model, a one-item list per list, a one-entry map per map, and so a
    list in an entry where a class declares a map of lists. A class is entered
    once per path (*inside*).
    """
    for name, field_info in root.model_fields.items():
        if is_secret(field_info):
            yield {wire_name(root, name): SECRET}
    for place in held(root):
        if place.cls in (*inside, root) or place.cls not in secret_holders():
            continue
        for document in documents_with_a_secret(place.cls, (*inside, root)):
            yield {wire_name(root, place.name): nested(place.parts, document)}


def test_every_declared_secret_is_hidden_wherever_the_classes_reach_it() -> None:
    reached = 0
    for root in roots():
        for document in documents_with_a_secret(root):
            assert SECRET not in json.dumps(shown_value(document, root)), (
                root,
                document,
            )
            reached += 1
    # 697 documents from 90 roots: a fall is a secret the walk no longer reaches.
    assert reached >= 690


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


def test_a_value_is_shown_through_shown_value_only() -> None:
    # A patch to send back holds the values it writes: the redo patch's
    # bodies are named in full in ``client/exceptions.py``.
    assert callers_of("named_value") == {"core/names.py", "client/exceptions.py"}
    assert calls_of("named_value") == {
        "core/names.py": {"shown_value"},
        "client/exceptions.py": {"_named_change"},
    }
