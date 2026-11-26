"""The check that a QoS rule that backhauls to hubs names a hub.

A route action of a rule (``action.edge2EdgeRouteAction``,
``edge2DataCenterRouteAction`` or ``edge2CloudRouteAction``) whose
``routePolicy`` is ``"backhaul"`` and whose ``routeCfg.type`` is ``"edge"`` is a
hub backhaul: it sends its traffic to the hubs of its ``routeCfg.backhaulEdges``.
With none, the VCO stores a rule that backhauls nowhere. ``ConfigState.plan()``
refuses one that the edits made so (``BackhaulWithoutHubError``) and warns of
one that was so as read. Each of the three route actions is checked, whatever
the rule's ``routeType`` says; ``backhaulEdgesAutoSelection`` does not stand in
for a hub.
"""

import dataclasses
import functools
from collections.abc import Mapping, Sequence
from typing import Final, TypeIs

from pydantic import MISSING

from pyvelo.client.exceptions import BackhaulWithoutHubError, HublessBackhaul
from pyvelo.client.models.configuration.qos.rule import (
    QosRule,
    QosRuleAction,
    RouteAction,
    RouteConfig,
)
from pyvelo.client.schemas.configuration import AnyStoredModule
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.core import VcoModel, unwrap
from pyvelo.core.base import field_models, is_list
from pyvelo.core.fields import get_fixed_list
from pyvelo.core.rebase import Path
from pyvelo.core.references import Reference, patch_keys
from pyvelo.core.walk import walk_fields

ROUTE_ACTIONS: Final = (
    "edge_to_edge_route_action",
    "edge_to_data_center_route_action",
    "edge_to_cloud_route_action",
)
"""The route actions of a rule's ``action``, by their python names."""


def is_hub_backhaul(route_action: object) -> TypeIs[RouteAction]:
    """Whether *route_action* backhauls to hubs.

    That is a ``RouteAction`` whose ``route_policy`` is ``"backhaul"`` and whose
    ``route_cfg.type_`` is ``"edge"``.
    """
    return (
        isinstance(route_action, RouteAction)
        and route_action.route_policy == "backhaul"
        and route_action.route_cfg.type_ == "edge"
    )


def names_no_hub(route_action: RouteAction) -> bool:
    """Whether *route_action*'s ``route_cfg.backhaul_edges`` is unset or empty."""
    edges = route_action.route_cfg.backhaul_edges
    return edges is MISSING or not edges


@functools.cache
def _can_hold(model_cls: type[VcoModel], target: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* is a *target* or can hold one, at any depth.

    Computed once per pair of classes.
    """
    seen: set[type[VcoModel]] = set()
    pending = [model_cls]
    while pending:
        current = pending.pop()
        if current is target:
            return True
        if current in seen:
            continue
        seen.add(current)
        for field_info in current.model_fields.values():
            pending.extend(field_models(field_info))
    return False


def _segment_name(segment: VcoModel | None) -> str | None:
    """The ``name`` of a segment entry, or ``None`` when it has none."""
    metadata: object = getattr(segment, "segment", None)
    name: object = getattr(metadata, "name", None)
    return name if isinstance(name, str) else None


def _deletable(segment: VcoModel | None, action_path: Path) -> bool:
    """Whether a patch can delete the rule whose ``action`` is at *action_path*.

    The rule is an item of a list field of *segment* (``rules`` or
    ``defaults``); a ``FixedList`` one (the segment's default rules) takes no
    delete.
    """
    list_name = next(iter(action_path.parts[-3:-2]), None)
    if segment is None or not isinstance(list_name, str):
        return True
    field_info = type(segment).model_fields.get(list_name)
    return field_info is None or get_fixed_list(field_info) is None


def hubless_backhauls(module: str, data: VcoModel) -> tuple[HublessBackhaul, ...]:
    """Each route action of a rule in *data* that backhauls to hubs and names none.

    *data* is the data of the module named *module*. The places come without
    their patch keys. A module that holds no rule gives none.
    """
    found: list[HublessBackhaul] = []
    for site in walk_fields(
        data,
        holds=lambda model_cls: _can_hold(model_cls, QosRule),
        fields=lambda field_info: QosRuleAction in field_models(field_info),
    ):
        rule = site.model
        held: object = getattr(rule, site.name)
        action = unwrap(held)
        if not isinstance(rule, QosRule) or not isinstance(action, QosRuleAction):
            continue
        for name in ROUTE_ACTIONS:
            route_action: object = getattr(action, name)
            if is_hub_backhaul(route_action) and names_no_hub(route_action):
                found.append(
                    HublessBackhaul(
                        module,
                        site.path.child(name),
                        _segment_name(site.segment),
                        rule.name,
                        deletable=_deletable(site.segment, site.path),
                    )
                )
    return tuple(found)


def left_without_hub(
    references: Sequence[Reference], edited: Mapping[str, VcoModel]
) -> tuple[Reference, ...]:
    """The hub references among *references* whose hub backhaul would be left with none.

    A reference to a hub is an entry of a route action's
    ``route_cfg.backhaul_edges``. Taking out every entry that holds one of
    *references* leaves a rule that backhauls through hubs (``is_hub_backhaul``)
    with no hub when each entry of its list holds one; those references are
    returned, in order. A route action that does not backhaul through hubs
    counts for none. *edited* is the module data the references were found in,
    by module name.
    """
    hubs = [r for r in references if r.set_name == "backhaul_hub"]
    holders = {id(r.holder) for r in hubs if r.holder is not None}
    emptied: set[int] = set()
    for module in dict.fromkeys(r.module for r in hubs):
        data = edited.get(module)
        if data is None:
            continue
        for site in walk_fields(
            data,
            holds=lambda model_cls: _can_hold(model_cls, RouteAction),
            fields=lambda field_info: RouteConfig in field_models(field_info),
        ):
            if not is_hub_backhaul(site.model):
                continue
            held: object = site.model.route_cfg.backhaul_edges
            entries = unwrap(held)
            if is_list(entries) and all(id(e) in holders for e in entries):
                emptied.update(id(e) for e in entries)
    return tuple(r for r in hubs if id(r.holder) in emptied)


def check_backhaul_hubs(
    modules: Sequence[AnyStoredModule],
    as_read: Mapping[str, VcoModel | None],
) -> tuple[ValidationWarning, ...]:
    """Refuse a rule of the target that backhauls to hubs and names none.

    Warn of one that did as read.

    Each module is checked as edited (``module.data``) and, for what it finds,
    as read (*as_read*, by module name: ``ModulePlan.as_read``). A route action
    with no hub at the same path as read (the same route action of the same
    rule of the same segment, as ``check_references`` matches a reference) is
    left as it is with a ``backhaul_without_hub_kept`` warning; any other
    raises ``BackhaulWithoutHubError``, which names them all.
    """
    refused: list[HublessBackhaul] = []
    kept: list[HublessBackhaul] = []
    for module in modules:
        edited: object = module.data
        if not isinstance(edited, VcoModel):
            continue
        name = str(module.name)
        found = hubless_backhauls(name, edited)
        if not found:
            continue
        read = as_read.get(name)
        held = (
            {p.path for p in hubless_backhauls(name, read)}
            if read is not None
            else set()
        )
        for place in found:
            if place.path in held:
                kept.append(place)
            else:
                refused.append(
                    dataclasses.replace(place, keys=patch_keys(edited, place.path))
                )
    if refused:
        raise BackhaulWithoutHubError(refused)
    return tuple(
        ValidationWarning(
            path=f"{place.module}: {place.path}",
            message=(
                f"{place.module} {place.path}: rule {place.rule_name!r} sends its "
                "traffic by backhaul and names no hub (it was so as read; the save "
                "leaves it)"
            ),
            code="backhaul_without_hub_kept",
        )
        for place in kept
    )
