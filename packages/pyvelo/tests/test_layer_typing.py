# pyright: reportUnnecessaryTypeIgnoreComment=error, reportUnusedCallResult=false
"""Static tests: what each field kind gives on each layer, as the type checker sees it.

basedpyright is the test. Each ``assert_type`` is a cell of the table that
allows the access, and each ``pyright: ignore`` marks an access the table
forbids: with ``reportUnnecessaryTypeIgnoreComment`` an ignore that hides no
error is itself an error. ``_cases`` is never called.
"""

from typing import assert_type

from layered_models import Flagged, Mod, Reset, Rule, Seg, Settings
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.client.schemas.edge import CompositeFirewallModule, CompositeQosModule
from pyvelo.core import (
    Composed,
    Edge,
    Effective,
    Owned,
    OwnedGroup,
    Profile,
    effective_view,
)
from pyvelo.core.effective import Resolved
from pyvelo.core.owned import GroupHandle


def _cases(e: Mod[Edge], p: Mod[Profile], v: Mod[Effective], c: Mod[Composed]) -> None:  # noqa: PLR0915 (existing: split it when it next changes)
    # structural
    assert_type(e.segments, list[Seg[Edge]])
    assert_type(p.segments, list[Seg[Profile]])
    assert_type(v.segments, list[Seg[Effective]])
    assert_type(c.segments, list[Seg[Composed]])

    # Inheritable
    assert_type(e.enabled, Owned[bool])
    assert_type(p.enabled, bool)
    assert_type(v.enabled, Resolved[bool])
    assert_type(c.enabled, bool)
    assert_type(e.level, Owned[int | None])
    assert_type(p.level, int | None)
    assert_type(v.level, Resolved[int | None])
    assert_type(e.settings, Owned[Settings])
    assert_type(p.settings, Settings)
    assert_type(v.settings, Resolved[Settings])
    assert_type(c.settings, Settings)

    # ByFlag fields, and the coupled group, whose handle is on the edge layer only
    assert_type(e.affinity, Owned[Reset])
    assert_type(p.affinity, Reset)
    assert_type(e.segments[0].mapping, Owned[Flagged])
    assert_type(v.segments[0].mapping, Resolved[Flagged])
    assert_type(e.pair, GroupHandle)
    assert_type(Mod.pair, OwnedGroup)
    _ = p.pair  # pyright: ignore[reportAttributeAccessIssue]
    _ = v.pair  # pyright: ignore[reportAttributeAccessIssue]
    _ = c.pair  # pyright: ignore[reportAttributeAccessIssue]

    # EdgeOnly
    assert_type(e.edge_note, str)
    assert_type(v.edge_note, Resolved[str])
    assert_type(c.edge_note, str)
    _ = p.edge_note  # pyright: ignore[reportAttributeAccessIssue]

    # ProfileOnly
    assert_type(p.profile_note, str)
    assert_type(v.profile_note, Resolved[str])
    assert_type(c.profile_note, str)
    _ = e.profile_note  # pyright: ignore[reportAttributeAccessIssue]

    # Unconverted
    assert_type(e.legacy, list[int])
    assert_type(p.legacy, list[int])
    assert_type(c.legacy, list[int])
    _ = v.legacy  # pyright: ignore[reportAttributeAccessIssue]

    # Additive and Unconverted on a segment, reached through the structural list
    assert_type(e.segments[0].rules, list[Rule])
    assert_type(p.segments[0].rules, list[Rule])
    assert_type(v.segments[0].rules, list[Resolved[Rule]])
    assert_type(c.segments[0].rules, list[Rule])
    assert_type(e.segments[0].legacy, int | None)
    _ = v.segments[0].legacy  # pyright: ignore[reportAttributeAccessIssue]

    # Class access gives the descriptor, not a value.
    _ = Mod.enabled

    # The view
    assert_type(effective_view(e, p, Mod[Effective]), Mod[Effective])
    assert_type(effective_view(None, p, Mod[Effective]), Mod[Effective])

    # Constructors
    _ = Mod[Profile](enabled=True)
    _ = Mod[Edge](enabled=Owned(True))
    _ = Mod[Edge]()
    _ = Mod[Edge](edge_note="x")  # a kind_default field is optional, and takes its type
    _ = Mod[Edge](edge_note=1)  # pyright: ignore[reportArgumentType]
    _ = Mod[Profile](enabled="x")  # pyright: ignore[reportArgumentType]


def _container_cases(
    edge_cfg: EdgeConfiguration,
    profile_cfg: EnterpriseConfigurationProfile,
    composite: CompositeFirewallModule,
    edge_module: AnyConfigurationModule[Edge],
) -> None:
    assert_type(edge_cfg.firewall, FirewallConfigurationModule[Edge])
    assert_type(edge_cfg.firewall.data, FirewallData[Edge])
    assert_type(edge_cfg.firewall.data.segments[0], FirewallSegment[Edge])
    assert_type(profile_cfg.firewall, FirewallConfigurationModule[Profile])
    assert_type(profile_cfg.firewall.data, FirewallData[Profile])
    assert_type(composite.data, FirewallData[Composed])
    assert_type(composite.raw_data, object)
    assert_type(
        edge_cfg.get_module(ConfigurationModuleName.FIREWALL),
        FirewallConfigurationModule[Edge],
    )
    assert_type(edge_cfg.firewall.mutable_data(), FirewallData[Edge])
    _ = edge_module


def _qos_cases(
    edge_cfg: EdgeConfiguration,
    profile_cfg: EnterpriseConfigurationProfile,
    composite: CompositeQosModule,
) -> None:
    edge_data: QosData[Edge] = edge_cfg.qos.data
    profile_segment: QosSegment[Profile] = profile_cfg.qos.data.segments[0]
    assert_type(edge_cfg.qos, QosConfigurationModule[Edge])
    assert_type(edge_data, QosData[Edge])
    assert_type(profile_cfg.qos, QosConfigurationModule[Profile])
    assert_type(profile_segment, QosSegment[Profile])
    assert_type(composite.data, QosData[Composed])
    assert_type(
        edge_cfg.get_module(ConfigurationModuleName.QOS), QosConfigurationModule[Edge]
    )
    assert_type(edge_cfg.qos.mutable_data(), QosData[Edge])
    assert_type(edge_cfg.qos.data.segment("Guest"), QosSegment[Edge])
    assert_type(profile_cfg.qos.data.get_segment(name="Guest"), QosSegment[Profile])
    _ = edge_cfg.qos.data.segments[0].rules  # an Unconverted field reads on each layer
    _ = profile_cfg.qos.data.segments[0].rules
    _ = QosData[Composed]  # a layer of its own


def test_the_typing_cases_module_imports() -> None:
    assert callable(_cases)
    assert callable(_container_cases)
    assert callable(_qos_cases)
