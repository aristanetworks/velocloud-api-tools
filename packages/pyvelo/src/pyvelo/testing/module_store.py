"""``ModuleStore``: a fake VCO that holds configuration module rows as JSON.

The store is an ``httpx`` handler, so a client built over it runs its own request
building, parsing and error mapping. It serves module updates and inserts and
``configuration/getConfiguration``, and answers them the way a VCO does: a
stale ``version`` is refused with the precondition error when the conditional
check is on and updates nothing when it is off, and ``returnData`` makes the
answer the module row as stored.
"""

import copy
import itertools
import json
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from pydantic import MISSING

from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    ConfigurationModuleBase,
)
from pyvelo.core import ConfigContext
from pyvelo.testing.fixtures import make_jsonrpc_response

PRECONDITION_FAILED = "UPDATE_CONFIGURATION_MODULE_PRECONDITION_FAILED"

type StoreHook = Callable[[dict[str, Any], dict[str, Any] | None], None]
"""Called with the row about to be stored and the row it replaces (None for an insert); changes the first in place."""

_FIRST_VERSION = 1_700_000_000_000
_FIXED_TIME = "2024-01-01T00:00:00.000Z"
_UPDATE_PATH = "configuration.updateConfigurationModule"


class RpcError(Exception):
    """A JSON-RPC error the store answers with."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        self.code: int = code
        self.message: str = message
        self.data: object = data
        super().__init__(f"{code}: {message}")


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
    and stores nothing. Each stored write gets a new ``version``, a decimal
    string from a counter. ``conditional_check`` is the VCO's check of the
    ``version`` sent; ``return_data`` False is a VCO that ignores
    ``returnData``.
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
    conditional_check: bool
    return_data: bool

    def __init__(
        self, *, conditional_check: bool = False, return_data: bool = True
    ) -> None:
        self.rows = {}
        self.configurations = {}
        self.requests = []
        self.on_store = []
        self.fail_writes = set()
        self.conditional_check = conditional_check
        self.return_data = return_data
        self._writes: int = 0
        self._versions: itertools.count[int] = itertools.count(_FIRST_VERSION)
        self._methods: dict[str, Callable[[dict[str, Any]], object]] = {
            "configuration/updateConfigurationModule": self._update,
            "configuration/insertConfigurationModule": self._insert,
            "configuration/getConfiguration": self._get_configuration,
        }

    def add_configuration(self, row: Mapping[str, Any]) -> None:
        """Hold the configuration *row*; the module rows under its ``modules``, if any, too."""
        stored = copy.deepcopy(dict(row))
        modules: list[dict[str, Any]] = stored.pop("modules", None) or []
        self.configurations[stored["id"]] = stored
        for module in modules:
            self.rows[module["id"]] = module

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
        return serve(params)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        method: str = body["method"]
        params: dict[str, Any] = body.get("params") or {}
        self.requests.append((method, params))
        try:
            answer = make_jsonrpc_response(result=self.handle(method, params))
        except RpcError as error:
            answer = make_jsonrpc_response(
                error={"code": error.code, "message": error.message, "data": error.data}
            )
        return httpx.Response(200, json=answer)

    # -- methods ------------------------------------------------------------

    def _update(self, params: dict[str, Any]) -> object:
        if self._count_write():
            raise RpcError(-32603, "simulated failure")
        module_id: int = params["id"]
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
        update: dict[str, Any] = params["_update"]
        new = copy.deepcopy(row)
        new["data"] = copy.deepcopy(update["data"])
        if "refs" in update:
            _set_refs(new, update["refs"])
        for hook in self.on_store:
            hook(new, row)
        new["version"] = self._new_version()
        self.rows[module_id] = new
        return copy.deepcopy(new) if self._returns_data(params) else {"rows": 1}

    def _insert(self, params: dict[str, Any]) -> object:
        if self._count_write():
            raise RpcError(-32603, "simulated failure")
        module_id = max(self.rows, default=0) + 1
        row: dict[str, Any] = {
            "id": module_id,
            "type": params.get("type", "ENTERPRISE"),
            "created": _FIXED_TIME,
            "modified": _FIXED_TIME,
            "description": params.get("description"),
            "configurationId": params["configurationId"],
            "enterpriseLogicalId": None,
            "schemaVersion": None,
            "version": self._new_version(),
            "metadata": None,
            "name": params["name"],
            "data": copy.deepcopy(params["data"]),
        }
        _set_refs(row, params.get("refs"))
        for hook in self.on_store:
            hook(row, None)
        self.rows[module_id] = row
        if self._returns_data(params):
            return copy.deepcopy(row)
        return {"id": module_id, "rows": 1}

    def _get_configuration(self, params: dict[str, Any]) -> object:
        config_id: int = params["id"]
        config = self.configurations.get(config_id)
        if config is None:
            raise _validation_error(
                "configuration.getConfiguration",
                f"configuration not found with ID: {config_id}",
            )
        answer = copy.deepcopy(config)
        if "modules" in (params.get("with") or ()):
            answer["modules"] = [
                copy.deepcopy(row)
                for row in self.rows.values()
                if row.get("configurationId") == config_id
            ]
        return answer

    # -- helpers ------------------------------------------------------------

    def _count_write(self) -> bool:
        """Number this write; whether it is one of ``fail_writes``."""
        self._writes += 1
        return self._writes in self.fail_writes

    def _new_version(self) -> str:
        return str(next(self._versions))

    def _returns_data(self, params: Mapping[str, Any]) -> bool:
        return bool(params.get("returnData")) and self.return_data


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
