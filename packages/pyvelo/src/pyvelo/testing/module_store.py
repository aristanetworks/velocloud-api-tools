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

The store also holds hubs. ``add_edge`` adds an Edge of the enterprise (served
by ``enterprise/getEnterpriseEdgeList``) and ``add_cluster`` an
``edgeHubCluster`` service object. The twelve role calls (``edge/enableEdgeFor...``,
``edge/disableEdgeFor...``, ``enterprise/enableClusterFor...`` and
``enterprise/disableClusterFor...``) change the services and the device
settings refs as the VCO does, and ``enterprise/getEnterpriseServices`` answers
them; see ``ModuleStore``.
"""

import copy
import functools
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
_HUB_REF = "deviceSettings:vpn:edgeHub"
_CLUSTER_REF = "deviceSettings:vpn:edgeHubCluster"
_BACKHAUL_REF = "deviceSettings:backHaulEdge"
_EDGE_HUB = "edgeHub"
_CLUSTER = "edgeHubCluster"
_CLUSTER_MEMBER = "edgeHubClusterMember"
_SEGMENT_OBJECT = "NETWORK_SEGMENT"
_FIRST_GENERATED_ID = 90_000
_EDGE_LIST_PATH = "enterprise.getEnterpriseEdgeList"
_ROLES = {
    "hub": "EdgeHub",
    "bridge": "EdgeToEdgeBridge",
    "backhaul": "BackHaul",
}
_ROLE_KEY = {"hub": "edgeHub", "bridge": "edgeToEdgeBridge", "backhaul": "backHaulEdge"}


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

    **Hubs.** ``add_edge`` holds an Edge, ``add_cluster`` a cluster; both are
    enterprise objects the services read answers. ``enterprise/getEnterpriseEdgeList``
    answers the Edges as one page. The twelve role calls take ``id`` (the Edge
    ID or the cluster ID), ``configurationId``, ``moduleId`` and
    ``segmentObjectId``. An Edge enable finds or makes the ``edgeHub`` service
    of that Edge, profile, module and segment, sets its role and ``edgeHub``,
    adds its ``deviceSettings:vpn:edgeHub`` row to the module's refs once, and
    changes neither the module's data nor its ``version``; an Edge that is a
    cluster member is refused with the ``validationError``. An Edge hub disable
    deletes the service and every ref row that points at it and answers
    ``{"rows": 1}``, an Edge bridge or backhaul disable clears its role and
    answers the service; with no service each answers ``null``. A cluster call
    changes ``data["<configurationId>"]["<segmentId>"].roles`` (``segmentId``
    is the ``segmentId`` in the data of the segment object), adds the
    ``deviceSettings:vpn:edgeHubCluster`` row once, has every row of the
    cluster (in any module and segment) show its data as it is now, and bumps
    the cluster's ``version``; a cluster hub disable for a profile the cluster
    has no data for is answered with the error the VCO gives and changes
    nothing. A service's
    ``configuration`` (with ``configuration``) lists the ref rows that point at
    it; its ``edgeQOSUsage`` (with ``configuration`` and ``edgeQOSUsage``) the
    ``deviceSettings:backHaulEdge`` rows of the QoS module of an Edge
    configuration (one an ``add_stack`` named an Edge for).

    ``vpn_check`` makes a device settings update of a profile refuse what the
    VCO refuses: ``DS00000020`` when a segment with ``vpn.enabled`` has a
    Branch-to-Hub list and hub rows (in the refs after the write) that differ,
    ``DS00000010`` when the list is enabled and there is no hub row, and
    ``DS00000011`` when it is not and there is an Edge hub row. An update
    whose refs leave out the row of an Edge hub service deletes that service,
    whatever ``vpn_check`` is. ``fail_calls`` holds method names: a call to one
    answers ``simulated failure`` once.
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
    edges: dict[int, dict[str, Any]]
    """Edge ID to Edge: ``id``, ``logicalId``, ``name`` and ``cluster`` (the cluster ID, or None)."""
    vpn_check: bool
    """Whether a profile device settings update is checked against the hub rows of its refs."""
    fail_calls: set[str]
    """Method names whose next call is answered with ``simulated failure``, and then removed."""

    def __init__(
        self,
        *,
        conditional_check: bool = False,
        return_data: bool = True,
        enterprise_id: int | None = None,
        issue_rule_ids: bool = False,
        vpn_check: bool = False,
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
        self.edges = {}
        self.vpn_check = vpn_check
        self.fail_calls = set()
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
            "enterprise/getEnterpriseEdgeList": self._get_enterprise_edge_list,
        }
        for role, suffix in _ROLES.items():
            for enable in (True, False):
                verb = "enable" if enable else "disable"
                edge_path = f"edge/{verb}EdgeFor{suffix}"
                cluster_path = f"enterprise/{verb}ClusterFor{suffix}"
                self._methods[edge_path] = functools.partial(
                    self._edge_role, edge_path, role, enable
                )
                self._methods[cluster_path] = functools.partial(
                    self._cluster_role, cluster_path, role, enable
                )

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

    def add_edge(
        self,
        edge_id: int,
        *,
        logical_id: str,
        name: str,
        cluster: int | None = None,
    ) -> None:
        """Hold an Edge of the enterprise.

        With *cluster*, the ID of a cluster, the Edge is a member of it: an
        ``edgeHubClusterMember`` service object points at the Edge, and the
        Edge cannot be made a hub of its own.
        """
        self.edges[edge_id] = {
            "id": edge_id,
            "logicalId": logical_id,
            "name": name,
            "cluster": cluster,
        }
        if cluster is not None:
            member_id = self._new_object_id()
            self.add_object(
                member_id,
                logical_id=_generated_logical_id(member_id),
                name=name,
                type_=_CLUSTER_MEMBER,
                data={"logicalId": logical_id},
            )
            self.objects[member_id]["edgeId"] = edge_id

    def add_cluster(self, cluster_id: int, *, logical_id: str, name: str) -> None:
        """Hold a hub cluster: an ``edgeHubCluster`` service object with no data, at ``version`` 1."""
        self.add_object(cluster_id, logical_id=logical_id, name=name, type_=_CLUSTER)
        self.objects[cluster_id]["version"] = 1

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
        if method in self.fail_calls:
            self.fail_calls.remove(method)
            raise RpcError(-32603, "simulated failure")
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
        if self.vpn_check and self._is_profile_device_settings(new):
            self._check_hub_lists(new)
        self._prepare(new, row)
        new["version"] = self._new_version()
        self.rows[module_id] = new
        self._delete_dropped_edge_hubs(row, new)
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
        kind = params.get("type")
        with_ = params.get("with") or ()
        return [
            self._service(obj, with_)
            for obj in self.objects.values()
            if obj["object"] == _SERVICE_OBJECT and kind in (None, obj["type"])
        ]

    def _get_enterprise_edge_list(self, params: dict[str, Any]) -> object:
        return {
            "metaData": {"more": False, "limit": params.get("limit")},
            "data": [
                {
                    key: edge[key]
                    for key in ("id", "logicalId", "name")
                    if edge[key] is not None
                }
                | self._enterprise_key()
                for edge in self.edges.values()
            ],
        }

    # -- hub role calls -----------------------------------------------------

    def _edge_role(
        self, path: str, role: str, enable: bool, params: dict[str, Any]
    ) -> object:
        """An Edge role call: *role* of the Edge ``id`` on a profile, module and segment, enabled or disabled."""
        path = path.replace("/", ".")
        edge_id: int = _required(params, "id", path)
        configuration_id: int = _required(params, "configurationId", path)
        module_id: int = _required(params, "moduleId", path)
        segment_object_id: int = _required(params, "segmentObjectId", path)
        service = self._edge_service(
            edge_id, configuration_id, module_id, segment_object_id
        )
        if not enable:
            if service is None:
                return None
            if role == "hub":
                self._delete_object(service["id"])
                return {"rows": 1}
            service["data"]["roles"][_ROLE_KEY[role]] = False
            self._refresh_hub_rows(service)
            return self._service(service, ())
        edge = self.edges.get(edge_id)
        if edge is None:
            raise _validation_error(path, f"edge not found with ID: {edge_id}")
        if edge["cluster"] is not None:
            raise _validation_error(
                path,
                f"edge {edge_id} is a member of cluster {edge['cluster']}; "
                f"enable the cluster, not the member",
            )
        module = self.rows.get(module_id)
        if module is None:
            raise _validation_error(path, f"module not found with ID: {module_id}")
        if service is None:
            service = self._new_edge_service(
                edge, configuration_id, module_id, segment_object_id
            )
        roles = service["data"]["roles"]
        roles[_ROLE_KEY["hub"]] = True
        roles[_ROLE_KEY[role]] = True
        self._ensure_hub_row(
            service, _HUB_REF, configuration_id, module_id, segment_object_id
        )
        return self._service(service, ())

    def _cluster_role(
        self, path: str, role: str, enable: bool, params: dict[str, Any]
    ) -> object:
        """A cluster role call: *role* of the cluster ``id`` on a profile, module and segment, enabled or disabled."""
        path = path.replace("/", ".")
        cluster_id: int = _required(params, "id", path)
        configuration_id: int = _required(params, "configurationId", path)
        module_id: int = _required(params, "moduleId", path)
        segment_object_id: int = _required(params, "segmentObjectId", path)
        cluster = self.objects.get(cluster_id)
        if cluster is None or cluster["type"] != _CLUSTER:
            raise _validation_error(path, f"cluster not found with ID: {cluster_id}")
        segment = self.objects.get(segment_object_id)
        segment_id = None if segment is None else segment["data"].get("segmentId")
        if segment_id is None:
            raise _validation_error(
                path, f"segment not found with object ID: {segment_object_id}"
            )
        data: dict[str, Any] = cluster["data"]
        key = str(configuration_id)
        if enable:
            module = self.rows.get(module_id)
            if module is None:
                raise _validation_error(path, f"module not found with ID: {module_id}")
            held = data.setdefault(key, {}).setdefault(
                str(segment_id), {"roles": _no_roles()}
            )
            held["roles"][_ROLE_KEY["hub"]] = True
            held["roles"][_ROLE_KEY[role]] = True
            self._ensure_hub_row(
                cluster, _CLUSTER_REF, configuration_id, module_id, segment_object_id
            )
        else:
            if role == "hub" and key not in data:
                raise RpcError(-32603, "Cannot convert undefined or null to object")
            held = data.get(key, {}).get(str(segment_id))
            if held is not None:
                if role == "hub":
                    held["roles"] = _no_roles()
                else:
                    held["roles"][_ROLE_KEY[role]] = False
                self._refresh_hub_rows(cluster)
        cluster["version"] += 1
        return self._service(cluster, ())

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

    def _enterprise_key(self) -> dict[str, int]:
        """``{"enterpriseId": ...}`` when the store knows its enterprise; else nothing."""
        if self.enterprise_id is None:
            return {}
        return {"enterpriseId": self.enterprise_id}

    def _new_object_id(self) -> int:
        """An object ID that no object has, from ``_FIRST_GENERATED_ID`` up."""
        return max(max(self.objects, default=0) + 1, _FIRST_GENERATED_ID)

    def _service(self, obj: dict[str, Any], with_: object) -> dict[str, Any]:
        """Service object *obj* as ``getEnterpriseServices`` answers it, with the *with_* lists asked for."""
        answer: dict[str, Any] = {
            "id": obj["id"],
            "logicalId": obj["logicalId"],
            "name": obj["name"],
            "type": obj["type"],
            "data": copy.deepcopy(obj["data"]),
            "created": _FIXED_TIME,
            **self._enterprise_key(),
        }
        if "edgeId" in obj:
            answer["edgeId"] = obj["edgeId"]
        if "version" in obj:
            answer["version"] = str(obj["version"])
        requested = cast(list[str], with_)
        if "configuration" in requested:
            answer["configuration"] = self._configuration_usage(obj)
            if "edgeQOSUsage" in requested:
                answer["edgeQOSUsage"] = self._qos_usage(obj)
        return answer

    def _pointing_rows(
        self, obj: dict[str, Any]
    ) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
        """Every ref row, in any module, that points at *obj*: the module row, the ref name and the row."""
        found: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        for module in self.rows.values():
            for name, row in _ref_pairs(module):
                if row.get("enterpriseObjectId") == obj["id"]:
                    found.append((module, name, row))
        return found

    def _configuration_usage(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        """The ``configuration`` entries of service *obj*: one for each ref row that points at it."""
        return [
            {
                "enterpriseObjectId": obj["id"],
                "id": row["configurationId"],
                "name": self.configurations.get(row["configurationId"], {}).get("name"),
                "moduleId": row["moduleId"],
                "segmentObjectId": row.get("segmentObjectId"),
            }
            for _, _, row in self._pointing_rows(obj)
        ]

    def _qos_usage(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        """The ``edgeQOSUsage`` entries of service *obj*: one for each backhaul row of an Edge configuration's QoS module."""
        owners = self._edge_configuration_owners()
        usage: list[dict[str, Any]] = []
        for module, name, row in self._pointing_rows(obj):
            edge = self.edges.get(owners.get(module["configurationId"], -1))
            if name != _BACKHAUL_REF or module.get("name") != "QOS" or edge is None:
                continue
            usage.append(
                {
                    "enterpriseObjectId": obj["id"],
                    "segmentObjectId": row.get("segmentObjectId"),
                    "ref": name,
                    "edgeId": edge["id"],
                    "name": edge["name"],
                    "logicalId": edge["logicalId"],
                    "profileId": module["configurationId"],
                }
            )
        return usage

    def _edge_configuration_owners(self) -> dict[int, int]:
        """Edge configuration ID to the ID of the Edge it belongs to, for the stacks added for an Edge."""
        return {
            configs[0]: edge_id
            for edge_id, configs in self.stacks.items()
            if edge_id is not None
        }

    def _edge_service(
        self,
        edge_id: int,
        configuration_id: int,
        module_id: int,
        segment_object_id: int,
    ) -> dict[str, Any] | None:
        """The ``edgeHub`` service of Edge *edge_id* on that profile, module and segment, or None.

        A service belongs there when it was made there, or when a row of that
        module and segment points at it.
        """
        module = self.rows.get(module_id)
        for obj in self.objects.values():
            if obj["type"] != _EDGE_HUB or obj.get("edgeId") != edge_id:
                continue
            made = (obj.get("configurationId"), obj.get("moduleId")) == (
                configuration_id,
                module_id,
            ) and obj.get("segmentObjectId") == segment_object_id
            held = module is not None and any(
                row.get("enterpriseObjectId") == obj["id"]
                and row.get("segmentObjectId") == segment_object_id
                for _, row in _ref_pairs(module)
            )
            if made or held:
                return obj
        return None

    def _new_edge_service(
        self,
        edge: dict[str, Any],
        configuration_id: int,
        module_id: int,
        segment_object_id: int,
    ) -> dict[str, Any]:
        """Hold a new ``edgeHub`` service of *edge* with no roles on that profile, module and segment."""
        object_id = self._new_object_id()
        self.add_object(
            object_id,
            logical_id=_generated_logical_id(object_id),
            name=edge["name"],
            type_=_EDGE_HUB,
            data={"logicalId": edge["logicalId"], "roles": _no_roles()},
        )
        service = self.objects[object_id]
        service.update(
            edgeId=edge["id"],
            configurationId=configuration_id,
            moduleId=module_id,
            segmentObjectId=segment_object_id,
        )
        return service

    def _ensure_hub_row(
        self,
        obj: dict[str, Any],
        ref: str,
        configuration_id: int,
        module_id: int,
        segment_object_id: int,
    ) -> None:
        """Have module *module_id* hold a *ref* row for *obj* in that segment, once; every row of *obj* shows it as it is now.

        The module's data and ``version`` stay as they are.
        """
        module = self.rows[module_id]
        pairs = _ref_pairs(module)
        held = any(
            (name, row.get("enterpriseObjectId"), row.get("segmentObjectId"))
            == (ref, obj["id"], segment_object_id)
            for name, row in pairs
        )
        if not held:
            stored = self._stored_row(
                {
                    "configurationId": configuration_id,
                    "moduleId": module_id,
                    "enterpriseObjectId": obj["id"],
                    "ref": ref,
                    "segmentObjectId": segment_object_id,
                }
            )
            if stored is not None:
                _set_refs(module, [*pairs, (ref, stored)])
        self._refresh_hub_rows(obj)

    def _refresh_hub_rows(self, obj: dict[str, Any]) -> None:
        """Have every ref row that points at *obj* show its roles as they are now."""
        for module in self.rows.values():
            pairs = _ref_pairs(module)
            changed = False
            for _, row in pairs:
                if row.get("enterpriseObjectId") == obj["id"]:
                    row["data"] = _row_data(obj)
                    changed = True
            if changed:
                _set_refs(module, pairs)

    def _delete_object(self, object_id: int) -> None:
        """Delete object *object_id* and every ref row, in any module, that points at it."""
        _ = self.objects.pop(object_id, None)
        for module in self.rows.values():
            pairs = _ref_pairs(module)
            kept = [p for p in pairs if p[1].get("enterpriseObjectId") != object_id]
            if len(kept) != len(pairs):
                _set_refs(module, kept)

    def _delete_dropped_edge_hubs(
        self, old: dict[str, Any], new: dict[str, Any]
    ) -> None:
        """Delete the Edge hub services whose row *old* had and *new* leaves out, as the VCO's cleanup does."""
        kept = {row.get("enterpriseObjectId") for _, row in _ref_pairs(new)}
        for name, row in _ref_pairs(old):
            target = self.objects.get(row.get("enterpriseObjectId", -1))
            if (
                name == _HUB_REF
                and target is not None
                and target["type"] == _EDGE_HUB
                and target["id"] not in kept
            ):
                self._delete_object(target["id"])

    def _is_profile_device_settings(self, row: dict[str, Any]) -> bool:
        """Whether *row* is the device settings module of a configuration that is no Edge's own."""
        return (
            row.get("name") == "deviceSettings"
            and row.get("configurationId") not in self._edge_configuration_owners()
        )

    def _check_hub_lists(self, new: dict[str, Any]) -> None:
        """Refuse the device settings row *new* as the VCO does when a segment's hub list and hub rows disagree."""
        data = new.get("data")
        entries: list[Any] = []
        if isinstance(data, dict):
            entries = cast(list[Any], cast(dict[str, Any], data).get("segments") or [])
        hub_rows = [
            (name, row)
            for name, row in _ref_pairs(new)
            if name in (_HUB_REF, _CLUSTER_REF)
        ]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry = cast(dict[str, Any], entry)
            mine = [
                (name, row)
                for name, row in hub_rows
                if row.get("segmentObjectId") in self._segment_objects(entry)
            ]
            vpn: dict[str, Any] = entry.get("vpn") or {}
            hub_list: dict[str, Any] = vpn.get("edgeToEdgeHub") or {}
            edge_rows = [row for name, row in mine if name == _HUB_REF]
            if hub_list.get("enabled") and not mine:
                raise _validation_error(
                    _UPDATE_PATH,
                    "the Branch-to-Hub list is enabled and the segment has no hub",
                    code="DS00000010",
                )
            if not hub_list.get("enabled") and edge_rows:
                raise _validation_error(
                    _UPDATE_PATH,
                    "the Branch-to-Hub list is not enabled and the segment has "
                    "an Edge hub",
                    code="DS00000011",
                )
            if vpn.get("enabled"):
                listed = {
                    str(hub.get("logicalId")).lower()
                    for hub in cast(list[dict[str, Any]], hub_list.get("vpnHubs") or [])
                }
                held = {
                    str(
                        row["data"]["logicalId"]
                        if name == _HUB_REF
                        else row["logicalId"]
                    ).lower()
                    for name, row in mine
                }
                if listed != held:
                    raise _validation_error(
                        _UPDATE_PATH,
                        "the Branch-to-Hub list and the hubs of the segment differ",
                        code="DS00000020",
                    )

    def _segment_objects(self, entry: dict[str, Any]) -> set[int]:
        """The IDs of the segment objects that device settings segment *entry* is."""
        segment: dict[str, Any] = entry.get("segment") or {}
        return {
            obj["id"]
            for obj in self.objects.values()
            if obj["object"] == _SEGMENT_OBJECT
            and (
                obj["data"].get("segmentId") == segment.get("segmentId")
                or obj["logicalId"] == segment.get("segmentLogicalId")
            )
        }

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
        for key in ("logicalId", "name", "type", "object"):
            stored[key] = copy.deepcopy(target[key])
        stored["data"] = _row_data(target)
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


def _no_roles() -> dict[str, bool]:
    """The roles of a hub that has none."""
    return {"edgeHub": False, "edgeToEdgeBridge": False, "backHaulEdge": False}


def _generated_logical_id(object_id: int) -> str:
    """The logical ID the store gives the service object *object_id* it makes."""
    return f"00000000-0000-4000-8000-{object_id:012d}"


def _ref_pairs(module: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The rows of *module*'s refs, each with its ref name; copies, to be given back with ``_set_refs``."""
    refs = module.get("refs")
    return [(name, row) for name, row in _all_pairs(refs)]


def _all_pairs(refs: object) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(refs, dict):
        return []
    return [
        (name, row)
        for name, value in cast(dict[str, Any], refs).items()
        for row in _flatten({name: value})
    ]


def _row_data(obj: dict[str, Any]) -> dict[str, Any]:
    """The ``data`` a ref row shows of *obj*: the object's own data, as the VCO does (a cluster's is by configuration and then segment)."""
    return copy.deepcopy(obj["data"])


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
