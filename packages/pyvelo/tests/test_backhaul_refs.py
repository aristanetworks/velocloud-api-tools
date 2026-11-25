"""A QoS rule's backhaul hub derives its ``deviceSettings:backHaulEdge`` row from the profile's service for that segment.

A rule's backhaul entry holds the hub's service for the profile and segment
(``logicalId``). The row that goes with it points at that service, which the
profile's device settings module has as the hub's row; ``plan_module`` finds it
there (``profile_device_settings=``), on an edge as on a profile.

The profile is configuration ``CONFIG``, with the Global Segment (object 501)
and Guest (object 502); the Edge's own QoS module is in ``EDGE_CONFIG``.
"""

import uuid
from collections.abc import Iterator
from typing import Any, Literal, cast, overload

import hub_helpers
import pytest
import segment_helpers
from hub_helpers import CLUSTER, HUB_A, HUB_B, Hub, backhaul_entry, backhaul_rule
from pydantic import MISSING
from pyvelo.client.exceptions import RefsDroppedError
from pyvelo.client.hub_services import (
    BACKHAUL_REF,
    CLUSTER_HUB_REF,
    EDGE_HUB_REF,
    HubServiceTable,
    missing_services,
)
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration import ref_owners as ref_owners_module
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge
from pyvelo.client.models.configuration.ref_owners import ref_owners
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.core import ConfigContext, VcoModel, walk_fields
from pyvelo.core.identity import HubService, HubServiceNotFoundError
from pyvelo.core.layers import Edge, Profile
from pyvelo.core.rebase import Path
from pyvelo.testing import make_module_json, store_of
from refs_helpers import (
    CONFIG,
    QOS_ID,
    SEG0,
    SEG0_OBJECT,
    SEG1_OBJECT,
    ds_module,
    segment_row,
)
from segment_helpers import GLOBAL, GUEST, Segment

from ._type_support import MockVcoClientFactory

EDGE_CONFIG = 200
EDGE_QOS_ID = 21
SERVICE_A = "a6a6a6a6-a6a6-4a6a-8a6a-a6a6a6a6a6a6"
SERVICE_B = "b6b6b6b6-b6b6-4b6b-8b6b-b6b6b6b6b6b6"
SERVICE_A_ID = 601
SERVICE_B_ID = 602
OTHER_REF = "test:other"
OTHER_ROW = {"enterpriseObjectId": 9, "ref": OTHER_REF, "logicalId": SERVICE_A}


def profile_ds(*, hub_rows: list[dict[str, Any]]) -> DeviceSettingsConfigurationModule:
    """The profile's device settings module: both segments, and *hub_rows* in the refs."""
    edge_rows = [r for r in hub_rows if r["ref"] == EDGE_HUB_REF]
    cluster_rows = [r for r in hub_rows if r["ref"] == CLUSTER_HUB_REF]
    refs: dict[str, Any] = {
        "deviceSettings:segment": [
            segment_row(0, SEG0_OBJECT, SEG0),
            segment_row(2, SEG1_OBJECT, GUEST[2]),
        ]
    }
    if edge_rows:
        refs[EDGE_HUB_REF] = edge_rows
    if cluster_rows:
        refs[CLUSTER_HUB_REF] = cluster_rows
    return ds_module(
        [hub_helpers.ds_with_vpn(GLOBAL), hub_helpers.ds_with_vpn(GUEST)], refs
    )


def service_row(
    hub: Hub, service_id: int, service_logical_id: str, segment: Segment = GLOBAL
) -> dict[str, Any]:
    return hub_helpers.hub_row(
        hub,
        service_id=service_id,
        service_logical_id=service_logical_id,
        segment=segment,
        roles=("hub", "backhaul"),
    )


def profile_services() -> DeviceSettingsConfigurationModule:
    """The profile's device settings with Hub-A and Hub-B as hubs of the Global Segment."""
    return profile_ds(
        hub_rows=[
            service_row(HUB_A, SERVICE_A_ID, SERVICE_A),
            service_row(HUB_B, SERVICE_B_ID, SERVICE_B),
        ]
    )


def qos_row(
    hub: Hub,
    service_id: int,
    service_logical_id: str,
    *,
    segment: Segment = GLOBAL,
    configuration_id: int = CONFIG,
    module_id: int = QOS_ID,
) -> dict[str, Any]:
    return hub_helpers.backhaul_row(
        hub,
        service_id=service_id,
        service_logical_id=service_logical_id,
        segment=segment,
        configuration_id=configuration_id,
        module_id=module_id,
    )


def rule(name: str, *entries: dict[str, Any]) -> dict[str, Any]:
    result = backhaul_rule(name, *entries)
    result["ruleLogicalId"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
    return result


type AnyQosModule = QosConfigurationModule[Edge] | QosConfigurationModule[Profile]
type AnyQosData = QosData[Edge] | QosData[Profile]


@overload
def qos_module(
    rules: dict[Segment, list[dict[str, Any]]],
    refs: dict[str, Any],
    *,
    level: Literal[ConfigContext.EDGE],
) -> QosConfigurationModule[Edge]: ...
@overload
def qos_module(
    rules: dict[Segment, list[dict[str, Any]]],
    refs: dict[str, Any],
    *,
    level: Literal[ConfigContext.PROFILE] = ...,
) -> QosConfigurationModule[Profile]: ...
def qos_module(
    rules: dict[Segment, list[dict[str, Any]]],
    refs: dict[str, Any],
    *,
    level: ConfigContext = ConfigContext.PROFILE,
) -> AnyQosModule:
    """A QoS module of the layer *level* names, with *rules* in its segments and *refs*."""
    edge = level is ConfigContext.EDGE
    row = make_module_json(
        "QOS",
        {
            "segments": [
                segment_helpers.qos_entry(segment, rules=segment_rules)
                for segment, segment_rules in rules.items()
            ]
        },
        module_id=EDGE_QOS_ID if edge else QOS_ID,
        configuration_id=EDGE_CONFIG if edge else CONFIG,
        refs=refs,
    )
    context = {"refs_loaded": True}
    if edge:
        return QosConfigurationModule[Edge].model_validate(row, context=context)
    return QosConfigurationModule[Profile].model_validate(row, context=context)


@overload
def edited(
    module: QosConfigurationModule[Edge], **entries: list[dict[str, Any]]
) -> QosData[Edge]: ...
@overload
def edited(
    module: QosConfigurationModule[Profile], **entries: list[dict[str, Any]]
) -> QosData[Profile]: ...
def edited(module: AnyQosModule, **entries: list[dict[str, Any]]) -> AnyQosData:
    """A copy of the module's data in which each rule named in *entries* has those backhaul entries."""
    data = module.data.model_copy(deep=True)
    for segment in data.segments:
        for held in segment.rules:
            if held.name in entries:
                route = held.action.edge_to_edge_route_action.route_cfg
                route.backhaul_edges = [
                    BackhaulEdge.model_validate(e) for e in entries[held.name]
                ]
    return data


def plan(
    module: AnyQosModule,
    data: AnyQosData,
    ds: DeviceSettingsConfigurationModule | None,
) -> ModulePlan:
    return plan_module(
        module,
        data,
        level=module.config_context,
        profile_device_settings=ds,
    )


def rows_of(plan_: ModulePlan, name: str = BACKHAUL_REF) -> list[dict[str, Any]]:
    assert plan_.refs is not None
    held = plan_.refs.get(name)
    if held is None:
        return []
    return cast("list[dict[str, Any]]", held if isinstance(held, list) else [held])


def new_row(
    service_id: int,
    segment: Segment = GLOBAL,
    *,
    configuration_id: int = CONFIG,
    module_id: int = QOS_ID,
) -> dict[str, Any]:
    return {
        "configurationId": configuration_id,
        "moduleId": module_id,
        "enterpriseObjectId": service_id,
        "ref": BACKHAUL_REF,
        "segmentObjectId": segment[3],
    }


# -- the table --------------------------------------------------------------


def test_the_table_reads_the_services_of_an_edge_hub_and_of_a_cluster() -> None:
    refs = {
        EDGE_HUB_REF: service_row(HUB_A, SERVICE_A_ID, SERVICE_A),
        CLUSTER_HUB_REF: [
            service_row(CLUSTER, CLUSTER.id, "ignored", GLOBAL),
            service_row(CLUSTER, CLUSTER.id, "ignored", GUEST),
        ],
    }

    table = HubServiceTable.from_refs(refs)

    assert table.services() == (
        HubService(
            SERVICE_A_ID,
            uuid.UUID(SERVICE_A),
            uuid.UUID(HUB_A.logical_id),
            GLOBAL[3],
            "edge",
            "Hub-A",
        ),
        HubService(
            CLUSTER.id,
            uuid.UUID(CLUSTER.logical_id),
            uuid.UUID(CLUSTER.logical_id),
            GLOBAL[3],
            "cluster",
            "Cluster-1",
        ),
        HubService(
            CLUSTER.id,
            uuid.UUID(CLUSTER.logical_id),
            uuid.UUID(CLUSTER.logical_id),
            GUEST[3],
            "cluster",
            "Cluster-1",
        ),
    )


def test_a_service_is_found_by_its_segment_and_its_logical_id() -> None:
    table = HubServiceTable.from_refs(profile_services().raw_refs)

    found = table.find(GLOBAL[3], uuid.UUID(SERVICE_A))

    assert found is not None
    assert found.object_id == SERVICE_A_ID
    assert table.find(GUEST[3], uuid.UUID(SERVICE_A)) is None
    assert table.find(GLOBAL[3], uuid.UUID(HUB_A.logical_id)) is None


def test_a_service_is_found_for_a_hub_by_the_hubs_logical_id() -> None:
    table = HubServiceTable.from_refs(profile_services().raw_refs)

    found = table.find_for_hub(GLOBAL[3], uuid.UUID(HUB_B.logical_id))

    assert found is not None
    assert found.object_id == SERVICE_B_ID
    assert table.find_for_hub(GUEST[3], uuid.UUID(HUB_B.logical_id)) is None


def test_refs_that_are_not_an_object_give_an_empty_table() -> None:
    assert HubServiceTable.from_refs(MISSING).services() == ()
    assert HubServiceTable.from_refs(None).services() == ()
    assert (
        HubServiceTable.from_refs({EDGE_HUB_REF: [{"logicalId": "x"}]}).services() == ()
    )


# -- the row ----------------------------------------------------------------


def test_adding_a_hub_to_a_rule_adds_its_row() -> None:
    held = qos_row(HUB_B, SERVICE_B_ID, SERVICE_B)
    module = qos_module(
        {GLOBAL: [rule("r1"), rule("r2", backhaul_entry(HUB_B, SERVICE_B))]},
        {BACKHAUL_REF: held, OTHER_REF: OTHER_ROW},
    )
    data = edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)])

    planned = plan(module, data, profile_services())

    assert rows_of(planned) == [held, new_row(SERVICE_A_ID)]
    assert planned.refs is not None
    assert planned.refs[OTHER_REF] == OTHER_ROW
    (change,) = planned.ref_changes
    assert (change.added, change.name, change.segment) == (
        True,
        BACKHAUL_REF,
        "Global Segment",
    )
    assert change.row == new_row(SERVICE_A_ID)
    assert change.segment_object_id == GLOBAL[3]
    assert planned.ref_lines() == [
        f"  + refs {BACKHAUL_REF} (Global Segment): {SERVICE_A}"
    ]
    assert planned.warnings == ()


def test_removing_the_last_use_removes_it() -> None:
    row_a = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    row_b = qos_row(HUB_B, SERVICE_B_ID, SERVICE_B)
    module = qos_module(
        {
            GLOBAL: [
                rule("r1", backhaul_entry(HUB_A, SERVICE_A)),
                rule("r2", backhaul_entry(HUB_B, SERVICE_B)),
            ]
        },
        {BACKHAUL_REF: [row_a, row_b], OTHER_REF: OTHER_ROW},
    )
    data = edited(module, r1=[])

    planned = plan(module, data, profile_services())

    assert rows_of(planned) == [row_b]
    assert planned.refs is not None
    assert planned.refs[OTHER_REF] == OTHER_ROW
    (change,) = planned.ref_changes
    assert (change.added, change.row) == (False, row_a)
    assert planned.ref_lines() == [
        f"  - refs {BACKHAUL_REF} (Global Segment): {SERVICE_A}"
    ]


def test_removing_the_only_row_of_the_name_leaves_the_name_out_of_the_refs() -> None:
    row_a = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    module = qos_module(
        {GLOBAL: [rule("r1", backhaul_entry(HUB_A, SERVICE_A))]},
        {BACKHAUL_REF: row_a, OTHER_REF: OTHER_ROW},
    )

    planned = plan(module, edited(module, r1=[]), profile_services())

    assert planned.refs == {OTHER_REF: OTHER_ROW}


def test_two_rules_using_one_hub_share_one_row() -> None:
    row_a = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    module = qos_module(
        {GLOBAL: [rule("r1", backhaul_entry(HUB_A, SERVICE_A)), rule("r2")]},
        {BACKHAUL_REF: row_a},
    )

    second = plan(
        module,
        edited(module, r2=[backhaul_entry(HUB_A, SERVICE_A)]),
        profile_services(),
    )

    assert second.ref_changes == ()
    assert rows_of(second) == [row_a]

    both = qos_module(
        {
            GLOBAL: [
                rule("r1", backhaul_entry(HUB_A, SERVICE_A)),
                rule("r2", backhaul_entry(HUB_A, SERVICE_A)),
            ]
        },
        {BACKHAUL_REF: row_a},
    )
    one_left = plan(both, edited(both, r2=[]), profile_services())

    assert one_left.ref_changes == ()
    assert rows_of(one_left) == [row_a]


def test_an_edge_rule_takes_the_profile_scoped_service() -> None:
    module = qos_module(
        {GLOBAL: [rule("r1")]}, {OTHER_REF: OTHER_ROW}, level=ConfigContext.EDGE
    )
    data = edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)])

    planned = plan(module, data, profile_services())

    assert rows_of(planned) == [
        new_row(SERVICE_A_ID, configuration_id=EDGE_CONFIG, module_id=EDGE_QOS_ID)
    ]
    (change,) = planned.ref_changes
    assert change.segment_object_id == GLOBAL[3]
    assert planned.refs is not None
    assert planned.refs[OTHER_REF] == OTHER_ROW


def test_an_edge_rule_that_loses_its_hub_removes_the_row_as_read() -> None:
    held = qos_row(
        HUB_A,
        SERVICE_A_ID,
        SERVICE_A,
        configuration_id=EDGE_CONFIG,
        module_id=EDGE_QOS_ID,
    )
    module = qos_module(
        {GLOBAL: [rule("r1", backhaul_entry(HUB_A, SERVICE_A))]},
        {BACKHAUL_REF: held},
        level=ConfigContext.EDGE,
    )

    planned = plan(module, edited(module, r1=[]), profile_services())

    (change,) = planned.ref_changes
    assert (change.added, change.row) == (False, held)
    assert planned.refs == {}


def test_a_cluster_rule_takes_the_cluster_object() -> None:
    ds = profile_ds(
        hub_rows=[
            service_row(CLUSTER, CLUSTER.id, "ignored", GLOBAL),
            service_row(CLUSTER, CLUSTER.id, "ignored", GUEST),
        ]
    )
    module = qos_module({GLOBAL: [rule("g1")], GUEST: [rule("v1")]}, {})
    data = edited(module, v1=[backhaul_entry(CLUSTER, CLUSTER.logical_id)])

    planned = plan(module, data, ds)

    assert rows_of(planned) == [new_row(CLUSTER.id, GUEST)]
    (change,) = planned.ref_changes
    assert (change.segment, change.value) == ("Guest", CLUSTER.logical_id)


def test_a_hub_with_no_service_in_that_segment_raises() -> None:
    module = qos_module({GUEST: [rule("v1")]}, {})
    data = edited(module, v1=[backhaul_entry(HUB_A, SERVICE_A)])

    with pytest.raises(HubServiceNotFoundError) as caught:
        _ = plan(module, data, profile_services())

    error = caught.value
    assert (error.hub, error.segment) == ("Hub-A", "Guest")
    assert error.path.startswith("segments[Guest].rules[v1].")
    message = str(error)
    assert "has no service for this profile in the segment 'Guest'" in message
    assert "save the hub first and the rule after" in message
    assert (
        '{"segments": {"Guest": {"vpn": {"edgeToEdgeHub": {"vpnHubs": '
        '{"+": {"_op": "add", "name": "Hub-A"}}}}}}}'
    ) in message


def test_no_profile_device_settings_raises() -> None:
    module = qos_module({GLOBAL: [rule("r1")]}, {})
    data = edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)])

    with pytest.raises(HubServiceNotFoundError) as caught:
        _ = plan(module, data, None)

    message = str(caught.value)
    assert "profile_device_settings=" in message
    assert "save the hub first and the rule after" in message
    assert '"vpnHubs"' in message


def test_an_entry_without_its_service_raises() -> None:
    entry = backhaul_entry(HUB_A, SERVICE_A)
    del entry["logicalId"]
    module = qos_module({GLOBAL: [rule("r1")]}, {})
    data = edited(module, r1=[entry])

    (path,) = missing_services(data)
    assert str(path) == (
        'segments["Global Segment"].rules[r1].action.edge_to_edge_route_action'
        f".route_cfg.backhaul_edges[{HUB_A.logical_id}]"
    )
    with pytest.raises(HubServiceNotFoundError) as caught:
        _ = plan(module, data, profile_services())

    error = caught.value
    assert (error.hub, error.segment) == ("Hub-A", "Global Segment")
    message = str(error)
    assert "names the hub 'Hub-A' and no service for it" in message
    assert "logicalId" in message
    assert "apply" in message
    assert '"logical_id": "<' in message


def test_an_entry_that_had_no_service_as_read_does_not_refuse_an_unrelated_edit() -> (
    None
):
    entry = backhaul_entry(HUB_A, SERVICE_A)
    del entry["logicalId"]
    module = qos_module({GLOBAL: [rule("r1", entry)]}, {})
    data = module.data.model_copy(deep=True)
    data.segments[0].rules[0].action.edge_to_edge_encryption = False

    planned = plan(module, data, profile_services())

    assert planned.operation == "update"
    assert planned.ref_changes == ()


def test_nothing_edited_sends_the_rows_as_read() -> None:
    held = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    refs = {BACKHAUL_REF: held, OTHER_REF: OTHER_ROW}
    module = qos_module({GLOBAL: [rule("r1", backhaul_entry(HUB_A, SERVICE_A))]}, refs)
    data = module.data.model_copy(deep=True)
    data.segments[0].rules[0].action.edge_to_edge_encryption = False

    planned = plan(module, data, profile_services())

    assert planned.operation == "update"
    assert planned.refs == refs
    assert planned.ref_changes == ()
    assert planned.ref_lines() == ["  refs: sent as read"]
    assert planned.warnings == ()


def test_a_row_the_data_no_longer_names_as_read_warns_and_stays() -> None:
    held = qos_row(HUB_A, SERVICE_A_ID, SERVICE_A)
    module = qos_module({GLOBAL: [rule("r1")]}, {BACKHAUL_REF: held})
    data = module.data.model_copy(deep=True)
    data.segments[0].rules[0].action.edge_to_edge_encryption = False

    planned = plan(module, data, profile_services())

    assert planned.refs == {BACKHAUL_REF: held}
    assert [w.code for w in planned.warnings] == ["ref_without_owner"]


# -- the check of the row ---------------------------------------------------


def a_service_store(module: AnyQosModule) -> Any:
    store = store_of(module)
    store.add_object(
        GLOBAL[3],
        logical_id=GLOBAL[2],
        name=GLOBAL[1],
        type_="REGULAR",
        object_="NETWORK_SEGMENT",
        data={"segmentId": GLOBAL[0]},
    )
    store.add_object(
        SERVICE_A_ID,
        logical_id=SERVICE_A,
        name="Hub-A",
        type_="edgeHub",
        data={"logicalId": HUB_A.logical_id, "roles": {}},
    )
    return store


def an_added_hub() -> tuple[QosConfigurationModule[Profile], ModulePlan]:
    module = qos_module({GLOBAL: [rule("r1")]}, {})
    data = edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)])
    return module, plan(module, data, profile_services())


async def test_the_returned_row_is_applied(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, planned = an_added_hub()
    store = a_service_store(module)
    client = await mock_vco_client(store)

    write = await client.write_module(module, planned)

    (row,) = [c for c in write.applied if c.part == "refs"]
    assert BACKHAUL_REF in str(row.path)
    stored = store.rows[module.id_]["refs"][BACKHAUL_REF]
    assert (stored["logicalId"], stored["segmentObjectId"]) == (SERVICE_A, GLOBAL[3])


async def test_the_returned_row_is_checked(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, planned = an_added_hub()
    store = a_service_store(module)
    store.skipped_objects.add(SERVICE_A_ID)
    client = await mock_vco_client(store)

    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, planned)

    (lost,) = caught.value.lost_edits
    assert lost.part == "refs"
    assert BACKHAUL_REF in str(lost.path)
    assert SERVICE_A in str(lost.path)


async def test_update_module_takes_the_profile_device_settings(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module = qos_module({GLOBAL: [rule("r1")]}, {})
    data = edited(module, r1=[backhaul_entry(HUB_A, SERVICE_A)])
    store = a_service_store(module)
    client = await mock_vco_client(store)

    with pytest.raises(HubServiceNotFoundError):
        _ = await client.update_module(module, data)
    assert store.requests == []

    stored = await client.update_module(
        module, data, profile_device_settings=profile_services()
    )

    row = cast("dict[str, Any]", store.rows[module.id_]["refs"][BACKHAUL_REF])
    assert (row["enterpriseObjectId"], row["logicalId"]) == (SERVICE_A_ID, SERVICE_A)
    assert stored is not module


# -- the walk ---------------------------------------------------------------


class _Visits:
    """What ``ref_owners`` asked of the walk: the classes it asked about, and the models it took fields of."""

    def __init__(self) -> None:
        self.asked: list[type[VcoModel]] = []
        self.walked: list[type[VcoModel]] = []


@pytest.fixture
def visits(monkeypatch: pytest.MonkeyPatch) -> _Visits:
    seen = _Visits()
    real_holds = ref_owners_module.can_hold_owner
    real_walk = walk_fields

    def holds(model_cls: type[VcoModel]) -> bool:
        seen.asked.append(model_cls)
        return real_holds(model_cls)

    def walk(data: VcoModel, **asked: Any) -> Iterator[Any]:
        for site in real_walk(data, **asked):
            seen.walked.append(type(site.model))
            yield site

    monkeypatch.setattr(ref_owners_module, "can_hold_owner", holds)
    monkeypatch.setattr(ref_owners_module, "walk_fields", walk)
    return seen


def test_the_walk_reaches_rule_lists_and_skips_what_holds_no_owner(
    visits: _Visits,
) -> None:
    from pyvelo.client.models.configuration.firewall.rules import (
        FirewallOutboundRule,
    )
    from pyvelo.client.models.configuration.qos.rule import (
        QosRule,
        QosRuleMatch,
    )

    firewall_entry = segment_helpers.firewall_entry(GLOBAL)
    (template,) = firewall_entry["outbound"]
    firewall_entry["outbound"] = [
        {**template, "name": f"fw{n}", "ruleLogicalId": str(uuid.uuid4())}
        for n in range(3000)
    ]
    firewall = FirewallData[Profile].model_validate({"segments": [firewall_entry]})

    assert ref_owners(firewall, ConfigContext.PROFILE) == ()

    assert visits.asked == [
        FirewallData[Profile]
    ]  # the root says no: no rule is looked at
    assert FirewallOutboundRule not in visits.walked
    assert visits.walked == []

    visits.asked.clear()
    visits.walked.clear()
    rules = [rule(f"q{n}") for n in range(3000)]
    rules[1234] = rule("q1234", backhaul_entry(HUB_A, SERVICE_A))
    qos = QosData[Profile].model_validate(
        {"segments": [segment_helpers.qos_entry(GLOBAL, rules=rules)]}
    )

    owners = ref_owners(qos, ConfigContext.PROFILE)

    assert [str(o.value) for o in owners] == [SERVICE_A]
    assert visits.asked.count(QosRule) == 1
    assert visits.asked.count(QosRuleMatch) == 1
    assert QosRuleMatch not in visits.walked  # a class that holds no owner
    # Sites are the marked fields only: the one field of the one entry, and
    # no field of the 3,000 rules and their defaults.
    assert visits.walked == [BackhaulEdge]


def test_the_owner_of_a_backhaul_entry_is_found_below_a_rule_with_its_segment() -> None:
    module = qos_module({GLOBAL: [rule("r1", backhaul_entry(HUB_A, SERVICE_A))]}, {})

    (owner,) = ref_owners(module.data, ConfigContext.PROFILE)

    assert owner.name == BACKHAUL_REF
    assert owner.identity_type is HubService
    assert (owner.segment_id, owner.segment_name) == (0, "Global Segment")
    assert owner.path == Path.parse(
        'segments["Global Segment"].rules[r1].action.edge_to_edge_route_action'
        f".route_cfg.backhaul_edges[{HUB_A.logical_id}].logical_id"
    )
    assert isinstance(owner.holder, BackhaulEdge)
