"""``ModuleStore``: a fake VCO that holds configuration module rows as JSON.

The store is an ``httpx`` handler, so a client built over it runs its own request
building, parsing and error mapping. It serves module updates and inserts,
``configuration/getConfiguration``, ``configuration/getConfigurationModules``,
``enterprise/getEnterpriseServices`` and, for an edge whose two configurations
it was told (``add_stack``), ``edge/getEdgeConfigurationStack``, and answers
them the way a VCO does: a stale ``version`` is refused with the precondition
error when the conditional check is on and updates nothing when it is off,
``returnData`` makes the answer the module row as stored, a request with a
required key missing is refused with the ``validationError`` and changes
nothing, and with ``issue_rule_ids`` on a stored write gets the rule IDs a VCO
issues.

Ref rows are stored, completed and served as the VCO does (Appendix D of the
design). The enterprise objects a row can point at are the store's
``objects`` (``add_object``). Refs *sent* on an update, or given on an insert,
replace the module's ordinary rows, which are the rows of every name but the
three ``APPEND_ONLY_REF_NAMES``; stored rows of those names are kept and sent
ones are added. A sent row is kept only with a ``configurationId``, a
``moduleId`` (an insert sets it first) and an ``enterpriseObjectId``, none
null, and with an object not in ``skipped_objects``, as the VCO skips a row
whose insert fails. A kept row is stored with a new ``id`` and, when its
object is known, the object's ``logicalId``, ``name``, ``type``, ``object`` and
``data`` (and a ``segmentLogicalId`` from its ``segmentObjectId``). Rows are
grouped under their ``ref``: one row as an object, several as a list. With no
refs sent the rows stay, except for an update of a ``deviceSettings`` or
``QOS`` module with segments and an entry in ``processor_refs``: an inline
processor then returns its own rows, which replace the ordinary ones as given.
A kept append-only row keeps its ``id``.
"""

import copy
import itertools
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
from pydantic import MISSING

from pyvelo.client.derived_refs import APPEND_ONLY_REF_NAMES
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
_MODULES_PATH = "configuration.getConfigurationModules"
_PROCESSOR_MODULES = ("deviceSettings", "QOS")
_SERVICE_OBJECT = "NETWORK_SERVICE"
_REQUIRED_ROW_IDS = ("configurationId", "moduleId", "enterpriseObjectId")
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
    objects: dict[int, dict[str, Any]]
    """Object ID to enterprise object: ``id``, ``logicalId``, ``name``, ``type``, ``object`` and ``data``."""
    skipped_objects: set[int]
    """Objects a sent ref row points at in vain: the row is skipped, as the VCO skips a row whose insert fails."""
    processor_refs: dict[int, dict[str, Any]]
    """Module ID to the refs an inline processor returns, which replace the ordinary rows of an update that sends none."""

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
        self.objects = {}
        self.skipped_objects = set()
        self.processor_refs = {}
        self._ref_ids: itertools.count[int] = itertools.count(1)
        self._writes: int = 0
        self._rule_count: int = 0
        self._versions: itertools.count[int] = itertools.count(_FIRST_VERSION)
        self._methods: dict[str, Callable[[dict[str, Any]], object]] = {
            "configuration/updateConfigurationModule": self._update,
            "configuration/insertConfigurationModule": self._insert,
            "configuration/getConfiguration": self._get_configuration,
            "edge/getEdgeConfigurationStack": self._get_edge_configuration_stack,
            "configuration/getConfigurationModules": self._get_configuration_modules,
            "enterprise/getEnterpriseServices": self._get_enterprise_services,
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

    def add_object(
        self,
        object_id: int,
        *,
        logical_id: str,
        name: str,
        type_: str,
        object_: str = _SERVICE_OBJECT,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Hold an enterprise object a ref row can point at; a service by default."""
        self.objects[object_id] = {
            "id": object_id,
            "logicalId": logical_id,
            "name": name,
            "type": type_,
            "object": object_,
            "data": copy.deepcopy(data) if data is not None else {},
        }

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
            self._replace_refs(new, update["refs"])
        elif self._processor_applies(row, data):
            self._replace_refs_by_processor(new)
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
        if params.get("refs"):
            self._replace_refs(row, params["refs"], module_id=module_id)
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

    def _get_configuration_modules(self, params: dict[str, Any]) -> object:
        config_id = _required(params, "configurationId", _MODULES_PATH)
        names = params.get("modules") or None
        no_data = bool(params.get("noData"))
        answer: list[dict[str, Any]] = []
        for row in self.rows.values():
            if row.get("configurationId") != config_id:
                continue
            if names is not None and row.get("name") not in names:
                continue
            copied = copy.deepcopy(row)
            if no_data:
                _ = copied.pop("data", None)
            answer.append(copied)
        return answer

    def _get_enterprise_services(self, params: dict[str, Any]) -> object:
        del params
        return [
            {
                "id": obj["id"],
                "logicalId": obj["logicalId"],
                "name": obj["name"],
                "type": obj["type"],
                "data": copy.deepcopy(obj["data"]),
                "enterpriseId": self.enterprise_id,
            }
            for obj in self.objects.values()
            if obj["object"] == _SERVICE_OBJECT
        ]

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

    def _processor_applies(self, row: dict[str, Any], data: object) -> bool:
        """Whether an update of *row* to *data*, sending no refs, has a processor return its own rows."""
        if row.get("name") not in _PROCESSOR_MODULES:
            return False
        if row["id"] not in self.processor_refs or not isinstance(data, dict):
            return False
        segments = cast(dict[str, Any], data).get("segments")
        return isinstance(segments, list) and bool(segments)

    def _replace_refs(
        self, row: dict[str, Any], sent: object, *, module_id: int | None = None
    ) -> None:
        """Replace the ordinary rows of *row* by the rows in *sent*, completing and skipping them.

        With *module_id*, every sent row gets it as its ``moduleId`` first.
        """
        pairs = _append_only_rows(row.get("refs"))
        ordinary: list[tuple[str, dict[str, Any]]] = []
        for entry in _flatten(sent):
            if module_id is not None:
                entry["moduleId"] = module_id
            stored = self._stored_row(entry)
            if stored is None:
                continue
            name = stored.get("ref") or "null"
            target = pairs if name in APPEND_ONLY_REF_NAMES else ordinary
            target.append((name, stored))
        _set_refs(row, [*ordinary, *pairs])

    def _replace_refs_by_processor(self, row: dict[str, Any]) -> None:
        """Replace the ordinary rows of *row* by the processor's, as given; the append-only rows stay."""
        given = copy.deepcopy(self.processor_refs[row["id"]])
        kept = _append_only_rows(row.get("refs"))
        kept += _append_only_rows(given)
        _set_refs(row, kept, base=given)

    def _stored_row(self, sent: dict[str, Any]) -> dict[str, Any] | None:
        """The row to store for *sent*, new ``id`` and all; None for a row the VCO skips.

        A row of a known object is stored as five fields and what the object
        completes; one of an unknown object, as sent.
        """
        if any(sent.get(key) is None for key in _REQUIRED_ROW_IDS):
            return None
        if sent["enterpriseObjectId"] in self.skipped_objects:
            return None
        target = self.objects.get(sent["enterpriseObjectId"])
        if target is None:
            return {**sent, "id": next(self._ref_ids)}
        stored: dict[str, Any] = {
            "id": next(self._ref_ids),
            "configurationId": sent["configurationId"],
            "moduleId": sent["moduleId"],
            "enterpriseObjectId": sent["enterpriseObjectId"],
            "ref": sent.get("ref"),
            "segmentObjectId": sent.get("segmentObjectId"),
        }
        for key in ("logicalId", "name", "type", "object", "data"):
            stored[key] = copy.deepcopy(target[key])
        segment_id = sent.get("segmentObjectId")
        segment = self.objects.get(segment_id) if segment_id is not None else None
        if segment is not None:
            stored["segmentLogicalId"] = segment["logicalId"]
        return stored

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


def _flatten(refs: object) -> list[dict[str, Any]]:
    """Copies of the rows in *refs*, an object from name to one row or a list of rows.

    Anything else, and an entry that is not an object, has no rows.
    """
    if not isinstance(refs, dict):
        return []
    found: list[dict[str, Any]] = []
    for value in cast(dict[str, Any], refs).values():
        for entry in cast(list[Any], value) if isinstance(value, list) else [value]:
            if isinstance(entry, dict):
                found.append(copy.deepcopy(cast(dict[str, Any], entry)))
    return found


def _append_only_rows(refs: object) -> list[tuple[str, dict[str, Any]]]:
    """The rows of *refs* under an append-only name, each with its name."""
    if not isinstance(refs, dict):
        return []
    return [
        (name, row)
        for name, value in cast(dict[str, Any], refs).items()
        if name in APPEND_ONLY_REF_NAMES
        for row in _flatten({name: value})
    ]


def _set_refs(
    row: dict[str, Any],
    pairs: list[tuple[str, dict[str, Any]]],
    *,
    base: dict[str, Any] | None = None,
) -> None:
    """Give *row* the refs of *pairs* (name and row), grouped by name; none removes them.

    *base* is refs kept as given, but for the append-only names.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, entry in pairs:
        grouped.setdefault(name, []).append(entry)
    refs: dict[str, Any] = {
        name: value
        for name, value in (base or {}).items()
        if name not in APPEND_ONLY_REF_NAMES
    }
    for name, rows in grouped.items():
        refs[name] = rows[0] if len(rows) == 1 else rows
    if refs:
        row["refs"] = refs
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
