"""``ConfigPipeline.compare_effective``: the effective view against the VCO's composed modules, offline."""

import copy
from typing import Annotated, Any

import pytest
from pydantic import MISSING, Field
from pyvelo.client.exceptions import ComposedModuleError, VcoError
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.core import (
    Additive,
    ByPresence,
    Default,
    Effective,
    Inheritable,
    KeyField,
    Layered,
    Origin,
    Profile,
    Secret,
    VcoModel,
    effective_dump,
    effective_view,
    kind_default,
)
from pyvelo.core.identity import NetworkServiceIdentity, ResolverNotLoadedError
from pyvelo.core.layers import L
from pyvelo.testing import make_module_json
from pyvelo_intent import (
    ComposedModuleError as IntentComposedModuleError,
)
from pyvelo_intent import (
    EffectiveCategory,
    EffectiveComparison,
    EffectiveDifference,
)
from pyvelo_intent.config.effective import (
    CONVERTED_MODULES,
    EFFECTIVE_IGNORES,
    FINDINGS,
    UNMODELED_KEYS,
    EffectiveIgnore,
    compare_module,
)
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import desc  # pyright: ignore[reportImplicitRelativeImport]

EDGE_ID = 42
ENTERPRISE_ID = 5
PROFILE_MODULE_ID = 2
Json = dict[str, Any]

OMITTED = (
    "the composer keeps only the segments in use on the edge "
    + "(VLANs, routed interfaces, loopbacks and the Global Segment)"
)
SECRET = "a secret value is not compared"
UNRESOLVED = (
    "the rule's security service group, or an object it names, "
    + "is not among the enterprise's security objects"
)
GROUP = "6f1d2c3a-0b4e-4c55-9a77-1e2d3c4b5a69"
CATEGORY: Json = {
    "allowAndLogCategories": [11, 12],
    "blockedCategories": [21],
    "unknownCategoryAction": "allow",
}
REPUTATION: Json = {
    "minReputationScore": 40,
    "allowAndLogReputations": [3],
    "unknownReputationAction": "block",
}
ATP: Json = {
    "atp_enabled": True,
    "ids_enabled": True,
    "ips_enabled": False,
    "atp_logging_enabled": True,
}
SECURITY_KEYS = ("atp_action", "urlCategoryFiltering", "urlReputationFiltering")
SECURITY_OBJECTS: dict[str, list[Json]] = {
    "securityServiceGroup": [
        {
            "type": "securityServiceGroup",
            "id": 100,
            "logicalId": GROUP.upper(),
            "data": {
                "urlCategoryFiltering": {"id": 7},
                "urlReputationFiltering": {"id": 8},
                "idps": {"id": 9},
            },
        }
    ],
    "urlCategoryFiltering": [
        {
            "type": "urlCategoryFiltering",
            "id": 7,
            "logicalId": "a0000000-0000-4000-8000-000000000000",
            "data": CATEGORY,
        }
    ],
    "urlReputationFiltering": [
        {
            "type": "urlReputationFiltering",
            "id": 8,
            "logicalId": "a1000000-0000-4000-8000-000000000000",
            "data": REPUTATION,
        }
    ],
    "idps": [
        {
            "type": "idps",
            "id": 9,
            "logicalId": "a2000000-0000-4000-8000-000000000000",
            "data": {"idsEnabled": True, "ipsEnabled": False, "logEnabled": True},
        }
    ],
}


def rule(name: str, action: str = "allow") -> Json:
    return {"name": name, "action": {"allow_or_deny": action}, "match": {}}


def inbound(name: str) -> Json:
    return {"name": name, "match": {}, "action": {"type": "port_forwarding"}}


def segment(sid: int, name: str, *rules: Json) -> Json:
    return {"segment": desc(sid, name), "outbound": list(rules)}


PROFILE: Json = {
    "firewall_enabled": True,
    "segments": [segment(0, "Global Segment", rule("p1")), segment(2, "Guest")],
}
EDGE: Json = {
    "segments": [segment(0, "Global Segment", rule("e1"))],
    "syslog_forwarding": True,
}


def compose(edge: Json | None, profile: Json) -> Json:
    """What the composer gives for the two layers: the edge's key where it has one, arrays concatenated, the edge's segments first."""
    edge = edge or {"segments": []}
    data: Json = {}
    for key in (*profile, *edge):
        if key not in ("segments", "inbound", "inboundV6"):
            data[key] = copy.deepcopy(edge[key] if key in edge else profile[key])
    if profile.get("firewall_enabled") is False:
        data["firewall_enabled"] = False
    for key in ("inbound", "inboundV6"):
        if key in edge or key in profile:
            data[key] = copy.deepcopy([*edge.get(key, []), *profile.get(key, [])])
    by_id = {s["segment"]["segmentId"]: s for s in profile["segments"]}
    segments: list[Json] = []
    for entry in edge["segments"]:
        theirs = by_id.pop(entry["segment"]["segmentId"], {"outbound": []})
        segments.append(
            copy.deepcopy(
                {
                    **theirs,
                    **entry,
                    "outbound": [*entry["outbound"], *theirs["outbound"]],
                }
            )
        )
    segments.extend(copy.deepcopy(list(by_id.values())))
    data["segments"] = segments
    return data


async def loaded(
    edge: Json | None = EDGE, profile: Json = PROFILE, *, composed: Json | None = None
) -> tuple[ConfigPipeline, ConfigState, MockVcoClient]:
    """A state loaded through a pipeline over a store that composes *edge* and *profile* (or answers *composed*) for the edge."""
    edge_rows = (
        []
        if edge is None
        else [
            make_module_json(
                "firewall",
                copy.deepcopy(edge),
                module_id=1,
                configuration_id=10,
                version="e1",
            )
        ]
    )
    edge_config = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "id": 10, "modules": edge_rows}
    )
    profile_config = EnterpriseConfigurationProfile.model_validate(
        {
            "name": "P",
            "id": 20,
            "modules": [
                make_module_json(
                    "firewall",
                    copy.deepcopy(profile),
                    module_id=PROFILE_MODULE_ID,
                    configuration_id=20,
                )
            ],
        }
    )
    client = MockVcoClient(stack=(edge_config, profile_config))
    client.store.composed[EDGE_ID] = {
        "firewall": compose(edge, profile) if composed is None else composed
    }
    pipeline = ConfigPipeline(client)
    state = await pipeline.load(EDGE_ID, enterprise_id=ENTERPRISE_ID)
    return pipeline, state, client


def composed_modules(client: MockVcoClient) -> Json:
    firewall = client.store.composed[EDGE_ID]["firewall"]
    assert isinstance(firewall, dict)
    return firewall  # pyright: ignore[reportUnknownVariableType]


def defaults(state: ConfigState) -> Json:
    """The view's display defaults, as the comparison reads them."""
    dumped, _ = effective_dump(state.effective.firewall)
    return dumped


def by_path(comparison: EffectiveComparison) -> dict[str, EffectiveDifference]:
    paths = [e.path for e in comparison.entries]
    assert len(paths) == len(set(paths)), paths
    return {e.path: e for e in comparison.entries}


def categories(comparison: EffectiveComparison) -> dict[str, EffectiveCategory]:
    return {e.path: e.category for e in comparison.entries}


# --- a matching view ------------------------------------------------------


async def test_a_matching_view_has_no_findings():
    pipeline, state, _ = await loaded()
    comparison = await pipeline.compare_effective(state)
    assert comparison.findings == ()
    assert comparison.entries  # the display defaults are reported as information
    assert {e.category for e in comparison.entries} == {EffectiveCategory.FACTORY}


async def test_the_comparison_loads_a_resolver_the_plan_needs_as_a_save_does(
    monkeypatch: pytest.MonkeyPatch,
):
    pipeline, state, _ = await loaded()
    plan = state.plan
    asked: list[type] = []

    def needs_the_services(*args: Any, **kwargs: Any):
        if NetworkServiceIdentity not in asked:
            raise ResolverNotLoadedError(NetworkServiceIdentity)
        return plan(*args, **kwargs)

    async def load_resolvers(_state: ConfigState, *identities: type) -> None:
        asked.extend(identities)

    monkeypatch.setattr(state, "plan", needs_the_services)
    monkeypatch.setattr(pipeline, "load_resolvers", load_resolvers)

    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    assert asked == [NetworkServiceIdentity]


async def test_a_resolver_that_stays_not_loaded_stops_the_comparison(
    monkeypatch: pytest.MonkeyPatch,
):
    pipeline, state, _ = await loaded()
    loads: list[type] = []

    def needs_the_services(*args: Any, **kwargs: Any):
        raise ResolverNotLoadedError(NetworkServiceIdentity)

    async def load_resolvers(_state: ConfigState, *identities: type) -> None:
        loads.extend(identities)

    monkeypatch.setattr(state, "plan", needs_the_services)
    monkeypatch.setattr(pipeline, "load_resolvers", load_resolvers)

    with pytest.raises(ResolverNotLoadedError):
        _ = await pipeline.compare_effective(state)
    assert loads == [NetworkServiceIdentity]  # one load, never two


async def test_an_edge_with_no_firewall_row_compares_the_profiles_values():
    pipeline, state, _ = await loaded(None)
    comparison = await pipeline.compare_effective(state)
    assert comparison.findings == ()


async def test_the_constants_say_what_is_converted_and_what_is_a_finding():
    assert CONVERTED_MODULES == ("firewall",)
    assert UNMODELED_KEYS == {"firewall": frozenset({"etm"})}
    assert EFFECTIVE_IGNORES == ()
    assert FINDINGS == {
        EffectiveCategory.DIFFERS,
        EffectiveCategory.CLIENT_ONLY,
        EffectiveCategory.SERVER_ONLY,
    }
    assert [c.value for c in EffectiveCategory] == [
        "differs",
        "client_only",
        "server_only",
        "factory",
        "ignored",
        "unconverted",
    ]


# --- one test per category --------------------------------------------------


async def test_differs():
    pipeline, state, client = await loaded()
    composed_modules(client)["syslog_forwarding"] = False
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert (finding.module, finding.path) == ("firewall", "syslog_forwarding")
    assert finding.category is EffectiveCategory.DIFFERS
    assert (finding.client, finding.server) == (True, False)
    assert (str(finding.origin), finding.source) == ("edge", "syslog_forwarding")
    line = next(
        ln for ln in comparison.render().splitlines() if "syslog_forwarding" in ln
    )
    assert "differs" in line
    assert "client true" in line
    assert "server false" in line
    assert "edge" in line


async def test_client_only():
    profile = {**PROFILE, "syslog_forwarding": True}
    pipeline, state, client = await loaded({"segments": []}, profile)
    del composed_modules(client)["syslog_forwarding"]
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.CLIENT_ONLY
    assert finding.path == "syslog_forwarding"
    assert finding.client is True
    assert finding.server is MISSING
    assert (str(finding.origin), finding.source) == ("profile", "syslog_forwarding")
    line = next(ln for ln in comparison.render().splitlines() if "client_only" in ln)
    assert "syslog_forwarding" in line
    assert "server (none)" in line
    assert "profile" in line


async def test_server_only():
    pipeline, state, client = await loaded()
    composed_modules(client)["composerKey"] = {"added": 1}
    services = defaults(state)["services"]
    composed_modules(client)["services"] = {**services, "extra": True}
    comparison = await pipeline.compare_effective(state)

    found = {e.path: e for e in comparison.findings}
    key = found["composerKey"]
    assert key.category is EffectiveCategory.SERVER_ONLY
    assert key.client is MISSING
    assert key.server == {"added": 1}
    assert (key.origin, key.source) == (None, None)
    # A key inside an object the view has is found at its own path.
    assert found["services.extra"].category is EffectiveCategory.SERVER_ONLY
    line = next(ln for ln in comparison.render().splitlines() if "composerKey" in ln)
    assert line.lstrip().startswith("server_only")
    assert "client (none)" in line


async def test_factory_where_the_server_has_no_value():
    pipeline, state, client = await loaded()
    composed_modules(client)["securityFeatures"] = defaults(state)["securityFeatures"]
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    found = by_path(comparison)
    services = found["services"]
    assert services.category is EffectiveCategory.FACTORY
    assert services.server is MISSING
    assert services.client == defaults(state)["services"]
    assert str(services.origin) == "factory"
    assert services.source == "services"
    # The same value as the server's is also information.
    same = found["securityFeatures"]
    assert same.category is EffectiveCategory.FACTORY
    assert same.server != MISSING
    line = next(ln for ln in comparison.render().splitlines() if " services" in ln)
    assert "factory" in line
    assert "display default" in line


async def test_a_wrong_display_default_is_differs():
    pipeline, state, client = await loaded()
    composed_modules(client)["stateful_firewall_enabled"] = True
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.DIFFERS
    assert finding.path == "stateful_firewall_enabled"
    assert (finding.client, finding.server) == (False, True)
    assert str(finding.origin) == "factory"


async def test_a_nested_key_the_server_lacks_under_a_display_default_is_information():
    pipeline, state, client = await loaded()
    services = copy.deepcopy(defaults(state)["services"])
    del services["ssh"]["allowSelectedIp"]
    composed_modules(client)["services"] = services
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    entry = by_path(comparison)["services.ssh.allowSelectedIp"]
    assert entry.category is EffectiveCategory.FACTORY
    assert entry.server is MISSING
    assert str(entry.origin) == "factory"
    assert entry.reason and "display default" in entry.reason


async def test_a_nested_value_that_differs_under_a_display_default_is_differs():
    pipeline, state, client = await loaded()
    services = copy.deepcopy(defaults(state)["services"])
    services["ssh"]["enabled"] = True
    composed_modules(client)["services"] = services
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.DIFFERS
    assert finding.path == "services.ssh.enabled"
    assert str(finding.origin) == "factory"


async def test_a_stored_empty_list_the_server_lacks_is_client_only():
    profile = {
        **PROFILE,
        "services": {"ssh": {"enabled": False, "allowSelectedIp": []}},
    }
    pipeline, state, client = await loaded(EDGE, profile)
    del composed_modules(client)["services"]["ssh"]["allowSelectedIp"]
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.CLIENT_ONLY
    assert finding.path == "services.ssh.allowSelectedIp"
    assert finding.client == []
    assert str(finding.origin) == "profile"


async def test_an_empty_list_only_the_server_has_is_server_only():
    profile = {**PROFILE, "services": {"ssh": {"enabled": False}}}
    pipeline, state, client = await loaded(EDGE, profile)
    composed_modules(client)["services"]["ssh"]["allowSelectedIp"] = []
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.SERVER_ONLY
    assert finding.path == "services.ssh.allowSelectedIp"
    assert finding.server == []


async def test_an_ignored_path_is_reported_with_its_reason():
    _, state, client = await loaded()
    server = composed_modules(client)
    server["syslog_forwarding"] = False
    server["segments"][0]["outbound"][0]["action"]["allow_or_deny"] = "deny"
    ignores = [
        EffectiveIgnore("firewall", "syslog_forwarding", "the VCO turns it off"),
        EffectiveIgnore("firewall", "segments[*].outbound[0]", "a known rewrite"),
        EffectiveIgnore("qos", "firewall_enabled", "another module's"),
    ]
    entries = compare_module(
        "firewall", state.effective.firewall, server, ignores=ignores
    )

    found = {e.path: e for e in entries}
    assert found["syslog_forwarding"].category is EffectiveCategory.IGNORED
    assert found["syslog_forwarding"].reason == "the VCO turns it off"
    assert (found["syslog_forwarding"].client, found["syslog_forwarding"].server) == (
        True,
        False,
    )
    inner = found["segments[0].outbound[0]"]
    assert inner.category is EffectiveCategory.IGNORED
    assert inner.reason == "a known rewrite"
    assert not [e for e in entries if e.category in FINDINGS]
    # An ignore of another module does not apply.
    assert "firewall_enabled" not in found
    comparison = EffectiveComparison(tuple(entries))
    assert comparison.findings == ()
    assert "the VCO turns it off" in comparison.render()


async def test_an_ignore_matches_whole_parts_only():
    _, state, client = await loaded()
    server = composed_modules(client)
    server["syslog_forwarding"] = False
    near = [EffectiveIgnore("firewall", "syslog", "a prefix of the key")]
    entries = compare_module("firewall", state.effective.firewall, server, ignores=near)
    assert [e.path for e in entries if e.category in FINDINGS] == ["syslog_forwarding"]


async def test_unconverted():
    pipeline, state, client = await loaded()
    server = composed_modules(client)
    server["inbound"] = [inbound("in1")]
    server["inboundV6"] = [inbound("in6")]
    server["etm"] = {"enabled": False}
    server["segments"][0]["firewall_logging_enabled"] = True
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    found = by_path(comparison)
    for path in (
        "inbound",
        "inboundV6",
        "etm",
        "segments[0].firewall_logging_enabled",
    ):
        entry = found[path]
        assert entry.category is EffectiveCategory.UNCONVERTED, path
        assert entry.client is MISSING
        assert entry.server != MISSING
        assert entry.reason
    line = next(ln for ln in comparison.render().splitlines() if " etm" in ln)
    assert "unconverted" in line


async def test_a_stored_rules_logging_flags_match_the_server():
    flags = {"hostedLoggingEnabled": False, "syslogForwardingEnabled": True}
    edge = {**EDGE, "segments": [segment(0, "Global Segment", {**rule("e1"), **flags})]}
    pipeline, state, _ = await loaded(edge)
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    found = by_path(comparison)
    for key in flags:
        assert f"segments[0].outbound[0].{key}" not in found


async def test_a_logging_flag_the_view_lacks_is_server_only():
    pipeline, state, client = await loaded()
    first = composed_modules(client)["segments"][0]["outbound"][0]
    first["hostedLoggingEnabled"] = False
    first["syslogForwardingEnabled"] = True
    comparison = await pipeline.compare_effective(state)

    found = by_path(comparison)
    for key in ("hostedLoggingEnabled", "syslogForwardingEnabled"):
        entry = found[f"segments[0].outbound[0].{key}"]
        assert entry.category is EffectiveCategory.SERVER_ONLY, key
        assert entry.client is MISSING
    assert len(comparison.findings) == 2


# --- the keys the composer adds to a rule that names a security group ------------


async def grouped(
    *, composer_adds: bool = True, group: str | None = GROUP, seed: bool = True
) -> tuple[ConfigPipeline, ConfigState, MockVcoClient]:
    """An edge whose first rule names *group*, composed with the security keys the group's objects give it."""
    edge_rule = {**rule("e1"), "securityServiceGroup": group}
    edge = {**EDGE, "segments": [segment(0, "Global Segment", edge_rule)]}
    pipeline, state, client = await loaded(edge)
    if seed:
        client.store.security_objects = copy.deepcopy(SECURITY_OBJECTS)
    if composer_adds:
        first = composed_modules(client)["segments"][0]["outbound"][0]
        first.update(
            atp_action=copy.deepcopy(ATP),
            urlCategoryFiltering=copy.deepcopy(CATEGORY),
            urlReputationFiltering=copy.deepcopy(REPUTATION),
        )
    return pipeline, state, client


def security_reads(client: MockVcoClient) -> list[str]:
    return [
        p["type"]
        for m, p in client.store.requests
        if m == "enterprise/getSecurityObjects"
    ]


async def test_the_composers_security_keys_match_the_group():
    pipeline, state, _ = await grouped()
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    for key in SECURITY_KEYS:
        assert not [e for e in comparison.entries if f"outbound[0].{key}" in e.path]


async def test_a_security_key_that_differs_from_its_object_is_differs():
    pipeline, state, client = await grouped()
    first = composed_modules(client)["segments"][0]["outbound"][0]
    first["urlCategoryFiltering"]["unknownCategoryAction"] = "block"
    first["atp_action"]["ips_enabled"] = True
    comparison = await pipeline.compare_effective(state)

    found = by_path(comparison)
    assert {e.path for e in comparison.findings} == {
        "segments[0].outbound[0].urlCategoryFiltering.unknownCategoryAction",
        "segments[0].outbound[0].atp_action.ips_enabled",
    }
    entry = found["segments[0].outbound[0].urlCategoryFiltering.unknownCategoryAction"]
    assert entry.category is EffectiveCategory.DIFFERS
    assert (entry.client, entry.server) == ("allow", "block")
    # It is the rule's origin, and the key's source is the rule's group.
    assert entry.origin is Origin.EDGE
    assert entry.source == "segments[0].outbound[0].securityServiceGroup"
    assert entry.reason is None


async def test_an_expected_security_key_the_server_lacks_is_client_only():
    pipeline, state, client = await grouped()
    first = composed_modules(client)["segments"][0]["outbound"][0]
    del first["urlReputationFiltering"]
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.CLIENT_ONLY
    assert finding.path == "segments[0].outbound[0].urlReputationFiltering"
    assert finding.client == REPUTATION
    assert finding.server is MISSING
    assert finding.origin is Origin.EDGE
    assert finding.source == "segments[0].outbound[0].securityServiceGroup"


async def test_a_profile_rules_security_keys_carry_the_profile_origin():
    profile_rule = {**rule("p1"), "securityServiceGroup": GROUP}
    profile = {**PROFILE, "segments": [segment(0, "Global Segment", profile_rule)]}
    pipeline, state, client = await loaded(profile=profile)
    client.store.security_objects = copy.deepcopy(SECURITY_OBJECTS)
    server_rule = composed_modules(client)["segments"][0]["outbound"][1]
    server_rule.update(
        atp_action=copy.deepcopy(ATP),
        urlCategoryFiltering={**CATEGORY, "unknownCategoryAction": "block"},
        urlReputationFiltering=copy.deepcopy(REPUTATION),
    )
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert (
        finding.path
        == "segments[0].outbound[1].urlCategoryFiltering.unknownCategoryAction"
    )
    assert finding.origin is Origin.PROFILE
    assert finding.source == "segments[0].outbound[0].securityServiceGroup"


async def test_a_security_key_on_a_rule_without_a_group_is_server_only():
    pipeline, state, client = await grouped(group=None, composer_adds=False)
    first = composed_modules(client)["segments"][0]["outbound"][0]
    first["atp_action"] = copy.deepcopy(ATP)
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.SERVER_ONLY
    assert finding.path == "segments[0].outbound[0].atp_action"
    assert finding.client is MISSING
    assert finding.reason is None


async def test_an_unknown_group_reports_its_reason():
    unknown = "00000000-0000-4000-8000-000000000000"
    pipeline, state, _ = await grouped(group=unknown)
    comparison = await pipeline.compare_effective(state)

    found = by_path(comparison)
    assert {e.path for e in comparison.findings} == {
        f"segments[0].outbound[0].{key}" for key in SECURITY_KEYS
    }
    for key in SECURITY_KEYS:
        entry = found[f"segments[0].outbound[0].{key}"]
        assert entry.category is EffectiveCategory.SERVER_ONLY, key
        assert entry.reason == UNRESOLVED
    assert UNRESOLVED in comparison.render()


async def test_an_object_the_group_names_that_is_missing_reports_its_reason():
    pipeline, state, client = await grouped()
    client.store.security_objects["idps"] = []
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.SERVER_ONLY
    assert finding.path == "segments[0].outbound[0].atp_action"
    assert finding.reason == UNRESOLVED


async def test_security_objects_are_read_only_when_a_rule_names_a_group():
    pipeline, state, client = await loaded()
    _ = await pipeline.compare_effective(state)
    assert security_reads(client) == []

    pipeline, state, client = await grouped()
    _ = await pipeline.compare_effective(state)
    assert sorted(security_reads(client)) == sorted(SECURITY_OBJECTS)
    reads = [
        p for m, p in client.store.requests if m == "enterprise/getSecurityObjects"
    ]
    assert {p["enterpriseId"] for p in reads} == {ENTERPRISE_ID}


async def test_another_rule_key_the_view_lacks_is_still_a_finding():
    pipeline, state, client = await loaded()
    composed_modules(client)["segments"][0]["outbound"][0]["somethingNew"] = 1
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.SERVER_ONLY
    assert finding.path == "segments[0].outbound[0].somethingNew"


class Credentials(VcoModel):
    user: str = ""
    password: Annotated[str, Secret()] = ""


class SecretData(Layered[L]):
    login: Annotated[
        Inheritable[Credentials], ByPresence(), Default(factory=Credentials)
    ] = Field(default=...)
    logins: Annotated[Additive[list[Credentials]], KeyField("user")] = kind_default(
        factory=list
    )


def test_a_secret_is_ignored_with_its_reason():
    profile = SecretData[Profile].model_validate(
        {
            "login": {"user": "u", "password": "s3cret-a"},
            "logins": [{"user": "v", "password": "s3cret-b"}],
        }
    )
    view = effective_view(None, profile, SecretData[Effective])
    server: dict[str, object] = {
        "login": {"user": "u", "password": "other-iv"},
        "logins": [{"user": "v", "password": "another-iv"}],
    }
    entries = compare_module("secret", view, server, ignores=())

    found = {e.path: e for e in entries}
    assert set(found) == {"login.password", "logins[0].password"}
    for entry in found.values():
        assert entry.category is EffectiveCategory.IGNORED
        assert entry.reason == SECRET
        assert (entry.client, entry.server) == ("***", "***")
        for secret in ("s3cret-a", "s3cret-b", "other-iv", "another-iv"):
            assert secret not in repr(entry)
    assert str(found["login.password"].origin) == "profile"
    assert "iv" not in EffectiveComparison(tuple(entries)).render().replace(SECRET, "")


# --- how items are matched --------------------------------------------------


async def test_a_segment_the_composer_omits_is_ignored():
    pipeline, state, client = await loaded()
    segments = composed_modules(client)["segments"]
    composed_modules(client)["segments"] = [
        s for s in segments if s["segment"]["segmentId"] != 2
    ]
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    entry = by_path(comparison)["segments[2]"]
    assert entry.category is EffectiveCategory.IGNORED
    assert entry.reason == OMITTED
    assert entry.server is MISSING
    assert OMITTED in comparison.render()


async def test_an_answer_with_no_segments_key_omits_every_segment():
    pipeline, state, client = await loaded()
    del composed_modules(client)["segments"]
    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    found = by_path(comparison)
    assert set(found) >= {"segments[0]", "segments[2]"}
    for path in ("segments[0]", "segments[2]"):
        assert found[path].category is EffectiveCategory.IGNORED
        assert found[path].reason == OMITTED
        assert found[path].server is MISSING
    assert "segments" not in found


async def test_a_segment_only_the_server_has_is_server_only():
    pipeline, state, client = await loaded()
    extra = segment(9, "Extra", rule("x"))
    composed_modules(client)["segments"].append(extra)
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.SERVER_ONLY
    assert finding.path == "segments[9]"
    assert finding.client is MISSING
    assert finding.server == extra


async def test_segment_order_is_not_compared():
    pipeline, state, client = await loaded()
    composed_modules(client)["segments"].reverse()
    first = await pipeline.compare_effective(state)
    assert first.findings == ()

    composed_modules(client)["segments"].reverse()
    second = await pipeline.compare_effective(state)
    assert {e.path for e in first.entries} == {e.path for e in second.entries}


async def test_a_difference_inside_a_segment_is_found_by_the_segments_id():
    pipeline, state, client = await loaded()
    composed_modules(client)["segments"].reverse()
    composed_modules(client)["segments"][0]["segment"]["name"] = "Renamed"
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.path == "segments[2].segment.name"
    assert (finding.client, finding.server) == ("Guest", "Renamed")


async def test_additive_rules_compare_by_position():
    pipeline, state, client = await loaded()
    # The view's order is the edge's rules, then the profile's.
    composed_modules(client)["segments"][0]["outbound"].reverse()
    comparison = await pipeline.compare_effective(state)

    found = {e.path: e for e in comparison.findings}
    first = found["segments[0].outbound[0].name"]
    second = found["segments[0].outbound[1].name"]
    assert (first.client, first.server) == ("e1", "p1")
    assert (second.client, second.server) == ("p1", "e1")
    assert (str(first.origin), first.source) == (
        "edge",
        "segments[0].outbound[0]",
    )
    assert (str(second.origin), second.source) == (
        "profile",
        "segments[0].outbound[0]",
    )


async def test_an_additive_rule_the_server_lacks_is_client_only():
    pipeline, state, client = await loaded()
    del composed_modules(client)["segments"][0]["outbound"][1]
    comparison = await pipeline.compare_effective(state)

    (finding,) = comparison.findings
    assert finding.category is EffectiveCategory.CLIENT_ONLY
    assert finding.path == "segments[0].outbound[1]"
    assert str(finding.origin) == "profile"


async def test_an_absent_list_is_an_empty_one():
    pipeline, state, client = await loaded()
    for entry in composed_modules(client)["segments"]:
        entry.pop("outbound", None)
    comparison = await pipeline.compare_effective(state)
    # Global has two rules the server lacks; Guest has none, which is no difference.
    assert [e.path for e in comparison.findings] == ["segments[0].outbound"]


# --- the preconditions ----------------------------------------------------------


async def test_pending_changes_raise_value_error():
    pipeline, state, client = await loaded()
    await pipeline.apply(state, module="firewall", patch={"syslog_forwarding": False})
    with pytest.raises(ValueError, match=r"not saved"):
        _ = await pipeline.compare_effective(state)
    assert not [m for m, _ in client.store.requests if "ComposedModules" in m]


async def test_a_profile_state_raises_value_error():
    profile_config = EnterpriseConfigurationProfile.model_validate(
        {
            "name": "P",
            "id": 20,
            "modules": [
                make_module_json(
                    "firewall", copy.deepcopy(PROFILE), configuration_id=20
                )
            ],
        }
    )
    state = ConfigState(
        edge_config=None,
        profile_config=profile_config,
        target_type="profile",
        resolver_context=ResolverContext(enterprise_id=ENTERPRISE_ID),
    )
    state.take_snapshot()
    pipeline = ConfigPipeline(MockVcoClient())
    with pytest.raises(ValueError, match=r"edge.*load\("):
        _ = await pipeline.compare_effective(state)


async def test_a_state_that_does_not_know_its_edge_raises_value_error():
    _, loaded_state, _ = await loaded()
    assert loaded_state.edge_config is not None
    state = ConfigState(
        edge_config=loaded_state.edge_config,
        profile_config=loaded_state.profile_config,
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=ENTERPRISE_ID),
    )
    with pytest.raises(ValueError, match=r"load\(edge_id\)"):
        _ = await ConfigPipeline(MockVcoClient()).compare_effective(state)


async def test_a_module_that_is_not_converted_raises_value_error():
    pipeline, state, _ = await loaded()
    with pytest.raises(ValueError, match=r"qos.*firewall"):
        _ = await pipeline.compare_effective(state, modules=["qos"])


async def test_modules_can_be_named():
    pipeline, state, client = await loaded()
    comparison = await pipeline.compare_effective(state, modules=["firewall"])
    assert comparison.findings == ()
    (params,) = [
        p for m, p in client.store.requests if m == "edge/getEdgeConfigurationModules"
    ]
    assert params["modules"] == ["firewall"]


async def test_a_null_composed_module_raises_composed_module_error():
    pipeline, state, client = await loaded()
    client.store.composed[EDGE_ID] = {"firewall": None}
    with pytest.raises(ComposedModuleError, match="null") as raised:
        _ = await pipeline.compare_effective(state)
    assert raised.value.module == "firewall"
    assert IntentComposedModuleError is ComposedModuleError


async def test_a_module_missing_from_the_answer_raises_composed_module_error():
    pipeline, state, client = await loaded()
    client.store.composed[EDGE_ID] = {}
    with pytest.raises(ComposedModuleError, match="no firewall module") as raised:
        _ = await pipeline.compare_effective(state)
    assert raised.value.module == "firewall"


async def test_an_edge_the_store_has_no_answer_for_is_an_error():
    pipeline, state, client = await loaded()
    del client.store.composed[EDGE_ID]
    with pytest.raises(VcoError):
        _ = await pipeline.compare_effective(state)


# --- what is sent -----------------------------------------------------------------


async def test_the_request_does_not_ask_for_encrypted_keys():
    pipeline, state, client = await loaded()
    _ = await pipeline.compare_effective(state)

    (params,) = [
        p for m, p in client.store.requests if m == "edge/getEdgeConfigurationModules"
    ]
    assert "encryptKeys" not in params
    assert params["edgeId"] == EDGE_ID
    assert params["enterpriseId"] == ENTERPRISE_ID
    assert params["modules"] == ["firewall"]


async def test_the_profile_half_is_read_again():
    pipeline, state, client = await loaded({"segments": EDGE["segments"]})
    # Someone changes the profile after the state was loaded, and the VCO composes it.
    client.store.other_writer(
        PROFILE_MODULE_ID,
        lambda row: row["data"].update(firewall_logging_enabled=True),
    )
    client.store.composed[EDGE_ID] = {
        "firewall": compose(
            {"segments": EDGE["segments"]},
            {**PROFILE, "firewall_logging_enabled": True},
        )
    }
    before = len(client.store.requests)

    comparison = await pipeline.compare_effective(state)

    assert comparison.findings == ()
    methods = [m for m, _ in client.store.requests[before:]]
    assert methods.count("configuration/getConfiguration") == 1
    assert methods.count("edge/getEdgeConfigurationModules") == 1


async def test_the_state_is_not_changed_by_a_comparison():
    pipeline, state, client = await loaded({"segments": EDGE["segments"]})
    client.store.other_writer(
        PROFILE_MODULE_ID,
        lambda row: row["data"].update(firewall_logging_enabled=True),
    )
    client.store.composed[EDGE_ID] = {
        "firewall": compose(
            {"segments": EDGE["segments"]},
            {**PROFILE, "firewall_logging_enabled": True},
        )
    }
    _ = await pipeline.compare_effective(state)

    assert state.plan().changed == ()
    # The state's own profile half is the one it was loaded with.
    logging = state.effective.firewall.firewall_logging_enabled
    assert (str(logging.origin), logging.value) == ("factory", False)


# --- the comparison's text --------------------------------------------------------


async def test_render_is_by_module_then_path_with_a_header_per_module():
    entries = (
        EffectiveDifference(
            "firewall",
            "b",
            1,
            2,
            None,
            None,
            EffectiveCategory.SERVER_ONLY,
            None,
        ),
        EffectiveDifference(
            "firewall", "a", 1, 2, None, None, EffectiveCategory.DIFFERS, None
        ),
        EffectiveDifference(
            "qos",
            "z",
            MISSING,
            "x" * 300,
            None,
            None,
            EffectiveCategory.UNCONVERTED,
            "not converted",
        ),
    )
    text = EffectiveComparison(entries).render()
    lines = text.splitlines()
    assert [ln for ln in lines if not ln.startswith(" ")] == ["firewall", "qos"]
    assert lines.index("firewall") < next(
        i for i, ln in enumerate(lines) if "differs" in ln
    )
    assert next(i for i, ln in enumerate(lines) if " a:" in ln) < next(
        i for i, ln in enumerate(lines) if " b:" in ln
    )
    assert "not converted" in text
    assert "x" * 300 not in text  # a long value is cut
    assert EffectiveComparison(()).render() == "no differences"
