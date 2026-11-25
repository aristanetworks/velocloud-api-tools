"""``ModuleStore``: a fake VCO that holds configuration module rows as JSON.

The store is an ``httpx`` handler, so a client built over it runs its own request
building, parsing and error mapping. It serves module updates and inserts,
``configuration/getConfiguration`` and, for an edge whose two configurations
it was told (``add_stack``), ``edge/getEdgeConfigurationStack``, and answers
them the way a VCO does: a stale ``version`` is refused with the precondition
error when the conditional check is on and updates nothing when it is off,
``returnData`` makes the answer the module row as stored, a request with a
required key missing is refused with the ``validationError`` and changes
nothing, and with ``issue_rule_ids`` on a stored write gets the rule IDs a VCO
issues.
"""

import copy
import itertools
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
from pydantic import MISSING

from pyvelo.client.exceptions import PRECONDITION_FAILED
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    ConfigurationModuleBase,
)
from pyvelo.core import ConfigContext
from pyvelo.testing.fixtures import make_jsonrpc_response

type StoreHook = Callable[[dict[str, Any], dict[str, Any] | None], None]
"""Called with the row about to be stored and the row it replaces (None for an insert); changes the first in place."""

_FIRST_VERSION = 1_700_000_000_000
_FIXED_TIME = "2024-01-01T00:00:00.000Z"
_UPDATE_PATH = "configuration.updateConfigurationModule"
_INSERT_PATH = "configuration.insertConfigurationModule"
_STACK_PATH = "edge.getEdgeConfigurationStack"
_RULE_ID = "ruleLogicalId"
_QOS_LISTS = ("rules", "defaults")


class RpcError(Exception):
    """A JSON-RPC error the store answers with."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        self.code: int = code
        self.message: str = message
        self.data: object = data
        super().__init__(f"{code}: {message}")


def _required(params: Mapping[str, Any], key: str, path: str) -> Any:
    """``params[key]``; the VCO's ``validationError`` naming *key* when it is missing."""
    if key not in params:
        raise _validation_error(path, f"missing required property: {key}")
    return params[key]


def _validation_error(path: str, message: str, *, code: str | None = None) -> RpcError:
    """The VCO's ``validationError``, with one entry; it has a ``code`` when given one."""
    entry: dict[str, str] = {"message": message, "path": path}
    if code is not None:
        entry = {"code": code, **entry}
    return RpcError(
        -32003,
        "validationError",
        {"valid": False, "error": [entry], "warning": []},
    )


class ModuleStore:
    """A fake VCO for module reads and writes, over rows held as JSON.

    A write is an update or an insert; writes are numbered together from 1,
    and a write whose number is in ``fail_writes`` is answered with an error
    and stores nothing; a write whose number is in ``lose_answers`` is stored
    and then not answered. A request with a required key missing is answered
    with the ``validationError`` naming the key; it takes no write number and
    changes nothing. Each stored write gets a new ``version``, a decimal string
    from a counter. ``conditional_check`` is the VCO's check of the ``version``
    sent; ``return_data`` False is a VCO that ignores ``returnData``.
    ``enterprise_id`` is the enterprise the rows belong to, for tests of a
    client that has to name it: a request that does not name it is answered
    with an error and changes nothing.

    With ``issue_rule_ids`` on, a stored write gives rule IDs as a VCO does: a
    firewall rule gets one when it has none, and a new one when its content
    changes (a rename keeps it); a QoS rule gets one only when it has none.
    A hook sees the IDs. A refused or failed write issues none.
    """

    rows: dict[int, dict[str, Any]]
    """Module ID to module row, as the VCO holds it."""
    configurations: dict[int, dict[str, Any]]
    """Configuration ID to configuration row, without ``modules``."""
    requests: list[tuple[str, dict[str, Any]]]
    """The method and params of every request, in order."""
    on_store: list[StoreHook]
    fail_writes: set[int]
    """The 1-based numbers of the writes answered with an error."""
    lose_answers: set[int]
    """The 1-based numbers of the writes that are handled and then not answered: the request raises ``httpx.ReadTimeout``."""
    conditional_check: bool
    return_data: bool
    enterprise_id: int | None
    """The enterprise every request has to name; None when requests need not name one."""
    issue_rule_ids: bool
    """Whether a stored write gives its rules the IDs a VCO gives them."""
    stacks: dict[int | None, tuple[int, int]]
    """Edge ID, or None for every edge, to the IDs of its edge and profile configurations."""

    def __init__(
        self,
        *,
        conditional_check: bool = False,
        return_data: bool = True,
        enterprise_id: int | None = None,
        issue_rule_ids: bool = False,
    ) -> None:
        self.rows = {}
        self.configurations = {}
        self.requests = []
        self.on_store = []
        self.fail_writes = set()
        self.lose_answers = set()
        self.conditional_check = conditional_check
        self.return_data = return_data
        self.enterprise_id = enterprise_id
        self.issue_rule_ids = issue_rule_ids
        self.stacks = {}
        self._writes: int = 0
        self._rule_count: int = 0
        self._versions: itertools.count[int] = itertools.count(_FIRST_VERSION)
        self._methods: dict[str, Callable[[dict[str, Any]], object]] = {
            "configuration/updateConfigurationModule": self._update,
            "configuration/insertConfigurationModule": self._insert,
            "configuration/getConfiguration": self._get_configuration,
            "edge/getEdgeConfigurationStack": self._get_edge_configuration_stack,
        }

    def add_configuration(self, row: Mapping[str, Any]) -> None:
        """Hold the configuration *row*; the module rows under its ``modules``, if any, too.

        A module without a ``configurationId`` gets the configuration's;
        ``ValueError`` for a row without an ``id``, for a module row without
        an ``id`` and for a module whose ``configurationId`` is another's.
        Nothing is stored then.
        """
        stored = copy.deepcopy(dict(row))
        if "id" not in stored:
            raise ValueError(
                "a configuration row needs an id; add an 'id' key to the row"
            )
        config_id: int = stored["id"]
        modules: list[dict[str, Any]] = stored.pop("modules", None) or []
        for module in modules:
            if "id" not in module:
                raise ValueError(
                    f"a module row needs an id; add an 'id' key to each module "
                    f"row under configuration {config_id}"
                )
            held = module.setdefault("configurationId", config_id)
            if held != config_id:
                raise ValueError(
                    f"module {module.get('id')} is given under configuration "
                    f"{config_id}, but its configurationId is {held}; give the "
                    f"module under configuration {held}, or drop its "
                    f"configurationId so it takes {config_id}"
                )
        self.configurations[config_id] = stored
        for module in modules:
            self.rows[module["id"]] = module

    def add_stack(
        self,
        edge_configuration_id: int,
        profile_configuration_id: int,
        *,
        edge_id: int | None = None,
    ) -> None:
        """Answer ``edge/getEdgeConfigurationStack`` for *edge_id* with these two configurations.

        The configurations are read as the store holds them at each request,
        with their module rows; ``edge_id=None`` answers for every edge that
        has no stack of its own.
        """
        self.stacks[edge_id] = (edge_configuration_id, profile_configuration_id)

    def other_writer(
        self, module_id: int, edit: Callable[[dict[str, Any]], None]
    ) -> None:
        """Change module *module_id* as another writer would: *edit* the row, then a new ``version``.

        No hook runs.
        """
        row = self.rows[module_id]
        edit(row)
        row["version"] = self._new_version()

    def handle(self, method: str, params: dict[str, Any]) -> object:
        """The result of one request; raises ``RpcError`` for an error answer."""
        serve = self._methods.get(method)
        if serve is None:
            raise RpcError(-32601, f"Method not found: {method}")
        named = params.get("enterpriseId")
        if self.enterprise_id is not None and named != self.enterprise_id:
            raise RpcError(
                -32603,
                f"the request names enterprise {named}, not {self.enterprise_id}",
            )
        return serve(params)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        method: str = body["method"]
        params: dict[str, Any] = body.get("params") or {}
        self.requests.append((method, params))
        writes_before = self._writes
        try:
            answer = make_jsonrpc_response(result=self.handle(method, params))
        except RpcError as error:
            answer = make_jsonrpc_response(
                error={"code": error.code, "message": error.message, "data": error.data}
            )
        else:
            if self._writes != writes_before and self._writes in self.lose_answers:
                raise httpx.ReadTimeout("the answer was lost", request=request)
        return httpx.Response(200, json=answer)

    # -- methods ------------------------------------------------------------

    def _update(self, params: dict[str, Any]) -> object:
        module_id: int = _required(params, "id", _UPDATE_PATH)
        update: dict[str, Any] = _required(params, "_update", _UPDATE_PATH)
        data = _required(update, "data", _UPDATE_PATH)
        if self._count_write():
            raise RpcError(-32603, "simulated failure")
        row = self.rows.get(module_id)
        if row is None:
            raise _validation_error(
                _UPDATE_PATH,
                f"module not found with ID: {module_id}",
                code="UPDATE_CONFIGURATION_MODULE_0002",
            )
        version = params.get("version")
        if version is not None and version != row["version"]:
            if self.conditional_check:
                raise _validation_error(
                    _UPDATE_PATH,
                    f"module {module_id} has changed since version {version}",
                    code=PRECONDITION_FAILED,
                )
            return copy.deepcopy(row) if self._returns_data(params) else {"rows": 0}
        new = copy.deepcopy(row)
        new["data"] = copy.deepcopy(data)
        if "refs" in update:
            _set_refs(new, update["refs"])
        self._prepare(new, row)
        new["version"] = self._new_version()
        self.rows[module_id] = new
        return copy.deepcopy(new) if self._returns_data(params) else {"rows": 1}

    def _insert(self, params: dict[str, Any]) -> object:
        configuration_id = _required(params, "configurationId", _INSERT_PATH)
        name = _required(params, "name", _INSERT_PATH)
        data = _required(params, "data", _INSERT_PATH)
        if self._count_write():
            raise RpcError(-32603, "simulated failure")
        module_id = max(self.rows, default=0) + 1
        row: dict[str, Any] = {
            "id": module_id,
            "type": params.get("type", "ENTERPRISE"),
            "created": _FIXED_TIME,
            "modified": _FIXED_TIME,
            "description": params.get("description"),
            "configurationId": configuration_id,
            "enterpriseLogicalId": None,
            "schemaVersion": None,
            "version": self._new_version(),
            "metadata": None,
            "name": name,
            "data": copy.deepcopy(data),
        }
        _set_refs(row, params.get("refs"))
        self._prepare(row, None)
        self.rows[module_id] = row
        if self._returns_data(params):
            return copy.deepcopy(row)
        return {"id": module_id, "rows": 1}

    def _get_configuration(self, params: dict[str, Any]) -> object:
        path = "configuration.getConfiguration"
        config_id: int = _required(params, "id", path)
        return self._configuration(
            config_id, path, with_modules="modules" in (params.get("with") or ())
        )

    def _get_edge_configuration_stack(self, params: dict[str, Any]) -> object:
        edge_id: int = _required(params, "edgeId", _STACK_PATH)
        ids = self.stacks.get(edge_id) or self.stacks.get(None)
        if ids is None:
            raise _validation_error(_STACK_PATH, f"edge not found with ID: {edge_id}")
        return [self._configuration(i, _STACK_PATH, with_modules=True) for i in ids]

    # -- helpers ------------------------------------------------------------

    def _configuration(
        self, config_id: int, path: str, *, with_modules: bool
    ) -> dict[str, Any]:
        """A copy of configuration *config_id*, with copies of its module rows when asked."""
        config = self.configurations.get(config_id)
        if config is None:
            raise _validation_error(
                path, f"configuration not found with ID: {config_id}"
            )
        answer = copy.deepcopy(config)
        if with_modules:
            answer["modules"] = [
                copy.deepcopy(row)
                for row in self.rows.values()
                if row.get("configurationId") == config_id
            ]
        return answer

    def _prepare(self, new: dict[str, Any], old: dict[str, Any] | None) -> None:
        """Give *new* its rule IDs, then run the hooks with it and a copy of *old*.

        A hook that raises leaves the rule counter where it was.
        """
        issued = self._rule_count
        try:
            if self.issue_rule_ids:
                self._issue_rule_ids(new, old)
            for hook in self.on_store:
                hook(new, copy.deepcopy(old))
        except BaseException:
            self._rule_count = issued
            raise

    def _next_rule_number(self) -> int:
        self._rule_count += 1
        return self._rule_count

    def _issue_rule_ids(self, new: dict[str, Any], old: dict[str, Any] | None) -> None:
        """The rule IDs of module row *new*, given *old*, the row it replaces."""
        if new.get("name") == "firewall":
            before = {
                rule[_RULE_ID]: rule
                for rules in _rule_lists(old, "firewall")
                for rule in rules
                if rule.get(_RULE_ID)
            }
            for rules in _rule_lists(new, "firewall"):
                for rule in rules:
                    held = rule.get(_RULE_ID)
                    if not held or (
                        held in before and _content(rule) != _content(before[held])
                    ):
                        rule[_RULE_ID] = f"rule-id-{self._next_rule_number():014d}"
        elif new.get("name") == "QOS":
            for rules in _rule_lists(new, "QOS"):
                for rule in rules:
                    if not rule.get(_RULE_ID):
                        rule[_RULE_ID] = str(uuid.UUID(int=self._next_rule_number()))

    def _count_write(self) -> bool:
        """Number this write; whether it is one of ``fail_writes``."""
        self._writes += 1
        return self._writes in self.fail_writes

    def _new_version(self) -> str:
        return str(next(self._versions))

    def _returns_data(self, params: Mapping[str, Any]) -> bool:
        return bool(params.get("returnData")) and self.return_data


def _rule_lists(row: dict[str, Any] | None, module: str) -> list[list[dict[str, Any]]]:
    """The lists of rules a VCO gives IDs to in the data of *row*, a module named *module*."""
    data = None if row is None else row.get("data")
    if not isinstance(data, dict):
        return []
    data = cast(dict[str, Any], data)
    segments = [
        cast(dict[str, Any], s)
        for s in cast(list[Any], data.get("segments") or [])
        if isinstance(s, dict)
    ]
    if module == "firewall":
        places = [(data, "inbound"), (data, "inboundV6")]
        places += [(s, "outbound") for s in segments]
    else:
        places = [(s, key) for s in segments for key in _QOS_LISTS]
    found: list[list[dict[str, Any]]] = []
    for holder, key in places:
        rules = holder.get(key)
        if isinstance(rules, list):
            found.append(
                [
                    cast(dict[str, Any], r)
                    for r in cast(list[Any], rules)
                    if isinstance(r, dict)
                ]
            )
    return found


def _content(rule: dict[str, Any]) -> dict[str, Any]:
    """*rule* without its name and ID: what a VCO compares to tell an edited rule from a renamed one."""
    return {k: v for k, v in rule.items() if k not in ("name", _RULE_ID)}


def _set_refs(row: dict[str, Any], refs: object) -> None:
    """Replace the refs of *row* with *refs*; an empty or null value removes them."""
    if refs:
        row["refs"] = copy.deepcopy(refs)
    else:
        _ = row.pop("refs", None)


def module_row(module: ConfigurationModuleBase) -> dict[str, Any]:
    """*module* as a row in wire form: its data and refs as read, no ``refs`` key when it had none."""
    if module.raw_data is MISSING:
        raise ValueError(f"module {module.id_} has no raw data to store")
    row = module.model_dump(mode="json", by_alias=True, exclude={"data", "refs"})
    row["data"] = copy.deepcopy(module.raw_data)
    if module.raw_refs is not MISSING:
        row["refs"] = copy.deepcopy(module.raw_refs)
    return row


def configuration_row(config: ConfigurationBase) -> dict[str, Any]:
    """*config* as a row in wire form, without ``modules``."""
    return config.model_dump(mode="json", by_alias=True, exclude={"modules"})


def as_stored[M: ConfigurationModuleBase](
    module: M, data: object, *, version: str = "v2", refs: object = MISSING
) -> M:
    """*module* as the VCO would return it: its row with *data*, validated from JSON.

    The row keeps the refs of *module* unless *refs* is given. The result has
    the class and config level of *module*.
    """
    row = module_row(module)
    row["data"] = data
    row["version"] = version
    if refs is not MISSING:
        row["refs"] = refs
    level = module.config_context
    context = None if level is None else {"config_level": level}
    return type(module).model_validate_json(json.dumps(row), context=context)


def store_of(*modules: ConfigurationModuleBase, **knobs: Any) -> ModuleStore:
    """A store that holds the rows of *modules*, and a configuration row for each ``configuration_id``.

    *knobs* are the keyword arguments of ``ModuleStore``. A module not inserted
    yet (``is_new``) has no row; its configuration has one. The configuration
    of an edge-level module is named as an edge's is.
    """
    store = ModuleStore(**knobs)
    for module in modules:
        config_id = module.configuration_id
        if config_id is not None and config_id not in store.configurations:
            edge = module.config_context is ConfigContext.EDGE
            name = "Edge Specific Profile" if edge else f"Profile {config_id}"
            store.add_configuration({"id": config_id, "name": name})
        if not module.is_new:
            store.rows[module.id_] = module_row(module)
    return store
