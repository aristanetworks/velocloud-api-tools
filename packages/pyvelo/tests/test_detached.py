"""Objects a save replaced are detached: they refuse assignment and ``merge_patch``."""

import copy
import pickle
from typing import Any, ClassVar, Self, cast

import pytest
from pydantic import ConfigDict, model_validator
from pyvelo.client.exceptions import DetachedObjectError, VcoError
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.device_settings.refs import DeviceSettingsRefs
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.core import ConfigContext, PatchError, VcoModel, add
from pyvelo.core.rebase import Path
from pyvelo.testing import make_module_json

GLOBAL: dict[str, Any] = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
}
FW_RAW: dict[str, Any] = {
    "firewall_enabled": True,
    "segments": [
        {
            "segment": GLOBAL,
            "outbound": [
                {"name": "allow-all", "match": {}, "action": {"allow_or_deny": "allow"}}
            ],
        }
    ],
}
NEW_RULE = add({"name": "new", "match": {}, "action": {"allow_or_deny": "deny"}})
PATCH: dict[str, object] = {
    "firewall_enabled": False,
    "segments": {"Global Segment": {"outbound": {"+": NEW_RULE}}},
}
REPLACED = (
    "this object was replaced by a save; take it from the state again "
    "(state.edge.<module> or state.profile.<module>)"
)


def firewall() -> tuple[FirewallData, FirewallSegment, FirewallOutboundRule]:
    """Firewall data with one segment and one outbound rule, and those two."""
    data = FirewallData.model_validate(copy.deepcopy(FW_RAW))
    segment = data.segments[0]
    return data, segment, segment.outbound[0]


def dump(model: VcoModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


class Checked(VcoModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(validate_assignment=True)

    count: int = 0


class Filled(VcoModel):
    name: str = ""
    upper: str = ""

    @model_validator(mode="after")
    def _fill_upper(self) -> Self:
        self.upper = self.name.upper()
        return self


def test_assignment_on_a_detached_object_raises():
    data, segment, rule = firewall()
    _ = data.mark_detached()

    with pytest.raises(DetachedObjectError) as exc:
        data.firewall_enabled = False
    assert str(exc.value) == f"FirewallData.firewall_enabled: {REPLACED}"
    assert exc.value.paths == []
    with pytest.raises(DetachedObjectError, match="FirewallSegment.firewall_logging"):
        segment.firewall_logging_enabled = True
    with pytest.raises(DetachedObjectError, match="FirewallOutboundRule.name") as exc:
        rule.name = "other"
    assert "take it from the state again" in str(exc.value)
    assert data.firewall_enabled is True
    assert segment.firewall_logging_enabled is None
    assert rule.name == "allow-all"

    # A copy of a detached object is detached too.
    copied = data.model_copy(deep=True)
    assert copied.is_detached and copied.segments[0].outbound[0].is_detached
    assert copy.deepcopy(rule).is_detached and copy.copy(rule).is_detached
    with pytest.raises(DetachedObjectError):
        copied.firewall_enabled = False

    # The guard comes before validate_assignment: no ValidationError for a bad value.
    checked = Checked()
    _ = checked.mark_detached()
    with pytest.raises(DetachedObjectError, match="Checked.count"):
        checked.count = "not a number"  # pyright: ignore[reportAttributeAccessIssue]
    assert checked.count == 0


def test_reading_a_detached_object_is_fine():
    data, segment, rule = firewall()
    before = dump(data)
    compact = data.model_dump(context={"compact": True})
    _ = data.mark_detached()

    assert data.firewall_enabled is True
    assert segment.segment.name == "Global Segment"
    assert rule.name == "allow-all"
    assert rule.action.allow_or_deny == "allow"
    assert dump(data) == before
    assert data.model_dump(context={"compact": True}) == compact


def test_an_object_that_is_not_marked_is_unaffected():
    data, segment, rule = firewall()

    data.firewall_enabled = False
    segment.firewall_logging_enabled = True
    rule.name = "other"

    assert data.firewall_enabled is False
    assert segment.firewall_logging_enabled is True
    assert rule.name == "other"
    assert data.is_detached is False
    assert segment.is_detached is False
    assert rule.is_detached is False


def test_merge_patch_on_a_detached_object_raises():
    data, segment, _ = firewall()
    before = dump(data)
    _ = data.mark_detached()

    with pytest.raises(DetachedObjectError) as exc:
        _ = data.merge_patch(PATCH)
    assert str(exc.value) == f"FirewallData.merge_patch: {REPLACED}"
    with pytest.raises(DetachedObjectError, match="FirewallSegment.merge_patch"):
        _ = segment.merge_patch({"outbound": {"+": NEW_RULE}})
    # Before anything else: a patch that names no field raises the same.
    with pytest.raises(DetachedObjectError):
        _ = data.merge_patch({"no_such_field": 1})
    assert dump(data) == before


def test_mark_detached_lists_every_model_with_its_place():
    data, segment, rule = firewall()
    then = dump(data)

    marked = data.mark_detached()

    places = dict(marked)
    assert len(places) == len(marked)  # each place once
    assert places[()] is data
    assert places[("segments", 0)] is segment
    assert places[("segments", 0, "outbound", 0)] is rule
    assert places[("securityFeatures",)] is data.security_features  # the dump key
    assert all(node.is_detached for _, node in marked)
    # Each place leads to the node's own dump in the dump of the whole.
    for place, node in marked:
        at: Any = then
        for part in place:
            at = at[part]
        assert at == dump(node), place


def test_a_module_marks_its_data_and_refs():
    ref_name = "deviceSettings:clientConnector:config"
    ref: dict[str, Any] = {
        "id": 5,
        "enterpriseObjectId": 6,
        "configurationId": 10,
        "moduleId": 1,
        "segmentObjectId": None,
        "ref": ref_name,
        "object": "CLIENT_CONNECTOR",
        "name": "connector",
        "logicalId": "00000000-0000-0000-0000-000000000009",
        "status": None,
        "data": None,
    }
    raw: dict[str, Any] = {
        "lan": {"networks": [], "interfaces": []},
        "segments": [{"segment": GLOBAL}],
    }
    module = DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", raw, refs={ref_name: ref})
    )
    refs = module.refs
    assert isinstance(refs, DeviceSettingsRefs)

    places = dict(module.mark_detached())

    assert places[()] is module
    assert places[("data",)] is module.data
    assert places[("refs",)] is refs
    assert places[("refs", ref_name)] is refs.client_connector_config
    assert module.is_detached and module.data.is_detached and refs.is_detached
    with pytest.raises(DetachedObjectError, match="DeviceSettingsData"):
        module.data.segments = []


def test_model_machinery_still_works():
    data, segment, rule = firewall()

    # The private attributes are not set yet, or are None.
    assert FirewallOutboundRule.__new__(FirewallOutboundRule).is_detached is False
    bare = FirewallOutboundRule.model_construct(name="built")
    object.__setattr__(bare, "__pydantic_private__", None)
    assert bare.is_detached is False

    built = FirewallOutboundRule.model_construct(name="built")
    built.name = "renamed"
    assert (built.name, built.is_detached) == ("renamed", False)

    copied = data.model_copy(deep=True)
    copied.firewall_enabled = False
    copied.segments[0].outbound[0].name = "copied"
    assert (copied.is_detached, copied.segments[0].outbound[0].is_detached) == (
        False,
        False,
    )
    updated = data.model_copy(update={"firewall_enabled": False})
    updated.syslog_forwarding = True
    assert (updated.firewall_enabled, updated.syslog_forwarding) == (False, True)

    loaded = cast(FirewallData, pickle.loads(pickle.dumps(data)))
    loaded.firewall_enabled = False
    assert loaded.is_detached is False

    assert Filled.model_validate({"name": "a"}).upper == "A"
    assert Filled(name="b").upper == "B"

    checked = Checked()
    checked.count = "3"  # pyright: ignore[reportAttributeAccessIssue]
    assert checked.count == 3

    assert data.for_edge() is data
    assert rule.config_context is ConfigContext.EDGE
    segment.firewall_logging_enabled = True

    # As ConfigPipeline.apply does: the patch on a deep copy first, then on the data.
    trial = data.model_copy(deep=True)
    _ = trial.merge_patch(PATCH)
    _ = data.merge_patch(PATCH)
    for merged in (trial, data):
        assert merged.firewall_enabled is False
        assert [r.name for r in merged.segments[0].outbound] == ["allow-all", "new"]
    with pytest.raises(PatchError):
        _ = data.merge_patch({"no_such_field": 1})


def test_the_error_is_a_vco_error_and_exported():
    import pyvelo
    import pyvelo.client

    assert issubclass(DetachedObjectError, VcoError)
    assert "DetachedObjectError" in pyvelo.client.__all__
    assert "DetachedObjectError" in pyvelo.__all__
    assert pyvelo.client.DetachedObjectError is DetachedObjectError
    assert pyvelo.DetachedObjectError is DetachedObjectError
    path = Path(("segments", 0, "outbound"))
    error = DetachedObjectError("changed", (path,))
    assert (str(error), error.paths) == ("changed", [path])
    assert DetachedObjectError("changed").paths == []
