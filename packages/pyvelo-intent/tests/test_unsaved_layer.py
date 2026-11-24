"""An edge state saves its edge layer: an edit of its profile layer is refused by ``plan()``.

The state is ``test_state_plan.make_state()``: edge configuration 10 with a
firewall module (1), profile 20 with a firewall module (2) and a QoS one (3).
"""

import pytest
from pyvelo_intent import LayerNotSavedError
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)
from test_write_back import (  # pyright: ignore[reportImplicitRelativeImport]
    changed,
    data_of,
    methods,
)


def test_plan_refuses_an_edit_of_the_profile_layer_of_an_edge_state():
    state = make_state()
    state.profile.firewall.firewall_enabled = False
    state.profile.firewall.segments[1].firewall_logging_enabled = True

    with pytest.raises(LayerNotSavedError) as exc:
        _ = state.plan()

    assert exc.value.layer == "profile"
    # firewall_enabled is a key the profile layer did not store: it is added
    # after the keys that were there.
    assert exc.value.fields == {
        "firewall": ["segments[Guest].firewall_logging_enabled", "firewall_enabled"]
    }
    assert str(exc.value) == (
        "this state saves its edge layer only, and its profile layer was edited: "
        "firewall (segments[Guest].firewall_logging_enabled, firewall_enabled). "
        "Those edits are not saved. To change the profile, load it with "
        "load_profile(20) and make the edits there. They are reported this once: "
        "the next plan or save goes ahead without them."
    )
    assert state.plan().changed == ()  # reported once


async def test_save_sends_nothing_when_the_profile_layer_was_edited():
    state = make_state()
    client = MockVcoClient.for_state(state)
    state.edge.firewall.firewall_enabled.set_local(False)
    state.profile.firewall.firewall_enabled = False

    with pytest.raises(LayerNotSavedError):
        _ = await ConfigPipeline(client).save(state)

    assert client.store.requests == []
    assert changed(state) == ["firewall"]  # the edge edit is still planned
    _ = await ConfigPipeline(client).save(state)
    assert methods(client.store) == ["updateConfigurationModule"]
    assert data_of(client.store, 1)["firewall_enabled"] is False
    assert "firewall_enabled" not in data_of(client.store, 2)  # the profile's row


def test_reading_the_profile_layer_is_no_edit():
    state = make_state()
    assert state.profile.firewall.segments[1].segment.name == "Guest"
    assert state.profile.qos.segments[0].rules == []
    assert state.plan().changed == ()


def test_a_later_profile_edit_is_reported_again():
    state = make_state()
    state.profile.firewall.firewall_enabled = False
    with pytest.raises(LayerNotSavedError):
        _ = state.plan()
    state.profile.firewall.syslog_forwarding = True
    with pytest.raises(LayerNotSavedError) as exc:
        _ = state.get_changed_modules()
    assert exc.value.fields == {"firewall": ["syslog_forwarding"]}


async def test_a_profile_state_plans_its_profile_edits():
    client = MockVcoClient.for_state(make_state())
    profile = await ConfigPipeline(client).load_profile(20)
    profile.profile.firewall.firewall_enabled = False
    assert changed(profile) == ["firewall"]
