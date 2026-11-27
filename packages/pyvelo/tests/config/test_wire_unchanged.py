"""What a set of saves sends, and the wire dump of the modules they load, pinned.

``golden/wire.json`` holds the requests that write (every method but a ``get``, each
with its whole params) of a set of saves over the mock store, and the
``model_dump(mode="json", by_alias=True)`` of each module of each state they load,
before any edit. A change to what pyvelo prints must leave both as they are: when
this test fails, what is sent to the VCO has changed.

The file is written once, with ``PYVELO_WRITE_GOLDEN=1``, and is not regenerated to
make a change pass.
"""

import copy
import json
import os
import pathlib
from collections.abc import Awaitable, Callable
from typing import Any, Final

import pytest
from hub_state import (
    DS,
    edit,
    hub_pipeline,
    removal_and_its_rule,
    rule_uses_hub,
)
from pyvelo.config import add, delete
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.core.base import VcoModel, is_dict, is_list
from pyvelo.testing import MockVcoClient, ModuleStore, make_module_json
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    desc,
    make_state,
)

GOLDEN: Final = pathlib.Path(__file__).parent / "golden" / "wire.json"

type Loaded = tuple[ConfigState, ConfigPipeline, ModuleStore]
"""A state as loaded, and the pipeline and store a save of it goes through."""


def _dumps(name: str, state: ConfigState) -> dict[str, object]:
    """The wire dump of each module's data of *state*, as ``name/level/module``."""
    dumps: dict[str, object] = {}
    levels = (("edge", state.edge_config), ("profile", state.profile_config))
    for level, config in levels:
        modules = [] if config is None else config.modules
        for module in [] if not is_list(modules) else modules:
            data: object = getattr(module, "data", None)
            assert isinstance(data, VcoModel), (name, level, module.name)
            dumps[f"{name}/{level}/{module.name}"] = data.model_dump(
                mode="json", by_alias=True
            )
    return dumps


def _written(store: ModuleStore, since: int) -> list[object]:
    """The requests that write the store got after its first *since*, whole."""
    return [
        [method, params]
        for method, params in store.requests[since:]
        if not method.split("/")[-1].startswith("get")
    ]


async def _hub_removal() -> Loaded:
    """Hub-A out of Guest's hub lists and out of the rule that backhauls to it."""
    pipeline, store, state = await removal_and_its_rule()
    return state, pipeline, store


async def _profile_firewall_edit() -> Loaded:
    state = await rule_uses_hub()
    pipeline, store = hub_pipeline(state)
    rule = {"name": "block", "action": {"allow_or_deny": "deny"}, "match": {}}
    await edit(
        state, "firewall", {"segments": {"Guest": {"outbound": {"block": add(rule)}}}}
    )
    return state, pipeline, store


async def _profile_device_settings_edit() -> Loaded:
    state = await rule_uses_hub()
    pipeline, store = hub_pipeline(state)
    syslog = {"enabled": True, "facilityCode": "local3"}
    await edit(state, DS, {"segments": {"Global Segment": {"syslog": syslog}}})
    return state, pipeline, store


def _rule(name: str, **extra: object) -> dict[str, Any]:
    return {"name": name, "action": {"allow_or_deny": "allow"}, "match": {}, **extra}


async def _edge_with_rules() -> ConfigState:
    """An edge state whose edge firewall has the rules ``a`` and ``b``."""
    raw = {
        "segments": [
            {
                "segment": desc(0, "Global Segment"),
                "outbound": [_rule("a", keep=1), _rule("b", keep=2)],
            }
        ]
    }
    row = make_module_json(
        "firewall", copy.deepcopy(raw), module_id=1, configuration_id=10, version="e1"
    )
    return make_state(edge_modules=[row])


async def _edge_rules_reordered() -> Loaded:
    state = await _edge_with_rules()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    deny = {"action": {"allow_or_deny": "deny"}, "match": {}}
    outbound = {"c": add(deny, first=True), "a": delete()}
    await edit(
        state, "firewall", {"segments": {"Global Segment": {"outbound": outbound}}}
    )
    return state, pipeline, client.store


async def _edge_without_modules() -> ConfigState:
    """An edge state whose edge has no module."""
    return make_state(edge_modules=[])


async def _edge_module_inserted() -> Loaded:
    state = await _edge_without_modules()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    deny = {"action": {"allow_or_deny": "deny"}, "match": {}}
    outbound = {"block-ssh": add(deny)}
    await edit(
        state, "firewall", {"segments": {"Global Segment": {"outbound": outbound}}}
    )
    return state, pipeline, client.store


SCENARIOS: Final[
    dict[
        str,
        tuple[Callable[[], Awaitable[ConfigState]], Callable[[], Awaitable[Loaded]]],
    ]
] = {
    "hub removal and its rule": (rule_uses_hub, _hub_removal),
    "profile firewall edit": (rule_uses_hub, _profile_firewall_edit),
    "profile device settings edit": (rule_uses_hub, _profile_device_settings_edit),
    "edge firewall rules added, deleted and moved": (
        _edge_with_rules,
        _edge_rules_reordered,
    ),
    "edge firewall module inserted": (_edge_without_modules, _edge_module_inserted),
}
"""Each scenario: the state it loads, as read, and that state edited for its save."""


def _numbered_rule_ids(document: object, seen: dict[object, str]) -> object:
    """*document* with each ``ruleLogicalId`` value as ``<rule-N>``.

    N counts the distinct values in the order a walk meets them (a mapping's keys
    sorted, a list in order), so one ID is one token wherever it repeats. The
    fixtures give rules random IDs, new in each process.
    """
    if is_dict(document):
        held: dict[object, object] = {}
        for key in sorted(document, key=str):
            value = document[key]
            if key == "ruleLogicalId" and value is not None:
                held[key] = seen.setdefault(value, f"<rule-{len(seen) + 1}>")
            else:
                held[key] = _numbered_rule_ids(value, seen)
        return held
    if is_list(document):
        return [_numbered_rule_ids(item, seen) for item in document]
    return document


async def wire_now() -> dict[str, object]:
    """The saves and the dumps of ``SCENARIOS`` as they are now.

    ``saves`` maps a scenario to the ``[method, params]`` of each request that writes,
    in the order the store got them; ``dumps`` maps ``scenario/level/module`` to the
    wire dump of that module as loaded.
    """
    saves: dict[str, object] = {}
    dumps: dict[str, object] = {}
    for name, (loaded, edited) in SCENARIOS.items():
        dumps |= _dumps(name, await loaded())
        state, pipeline, store = await edited()
        since = len(store.requests)
        _ = await pipeline.save(state)
        saves[name] = _written(store, since)
    numbered = _numbered_rule_ids({"dumps": dumps, "saves": saves}, {})
    assert isinstance(numbered, dict)
    return numbered


@pytest.fixture(autouse=True)
async def _write_golden() -> None:
    """Write the golden file from ``wire_now()`` when ``PYVELO_WRITE_GOLDEN=1``."""
    if os.environ.get("PYVELO_WRITE_GOLDEN") == "1":
        text = json.dumps(await wire_now(), indent=1, sort_keys=True)
        GOLDEN.parent.mkdir(exist_ok=True)
        _ = GOLDEN.write_text(text + "\n")


async def test_the_requests_of_a_set_of_saves_are_unchanged() -> None:
    assert json.loads(GOLDEN.read_text())["saves"] == (await wire_now())["saves"]


async def test_every_fixture_dumps_the_same_wire_json() -> None:
    assert json.loads(GOLDEN.read_text())["dumps"] == (await wire_now())["dumps"]
