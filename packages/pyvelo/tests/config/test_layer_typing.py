# pyright: reportUnnecessaryTypeIgnoreComment=error, reportUnusedCallResult=false
"""Static tests: the layer a state's view gives, as the type checker sees it.

basedpyright is the test, as in pyvelo's ``test_layer_typing``. ``_cases`` is
never called.
"""

from typing import assert_type

from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.schemas.configuration import FirewallConfigurationModule
from pyvelo.config.state import ConfigState, LayerView
from pyvelo.core import ConfigContext
from pyvelo.core.layers import Edge, Profile
from pyvelo.testing import make_firewall_module, make_firewall_segment
from test_state_plan import make_state  # pyright: ignore[reportImplicitRelativeImport]


def _cases(state: ConfigState) -> None:
    assert_type(state.edge, LayerView[Edge])
    assert_type(state.profile, LayerView[Profile])
    assert_type(state.edge.firewall, FirewallData[Edge])
    assert_type(state.profile.firewall, FirewallData[Profile])
    q: QosData[Edge] = state.edge.qos
    assert_type(q, QosData[Edge])
    assert_type(state.profile.qos, QosData[Profile])


def test_the_typing_cases_module_imports() -> None:
    assert callable(_cases)


def test_a_state_view_gives_the_data_of_its_layer() -> None:
    state = make_state()
    assert type(state.edge.firewall) is FirewallData[Edge]
    assert type(state.profile.firewall) is FirewallData[Profile]
    assert state.edge.firewall.config_context is ConfigContext.EDGE
    assert state.profile.firewall.config_context is ConfigContext.PROFILE
    assert type(state.edge.qos) is QosData[Edge]
    assert type(state.profile.qos) is QosData[Profile]


def test_the_test_builders_take_the_layer_they_build() -> None:
    edge = make_firewall_module(mod_id=1, config_id=10)
    profile = make_firewall_module(mod_id=2, config_id=20, layer=Profile)
    assert type(edge) is FirewallConfigurationModule[Edge]
    assert type(profile) is FirewallConfigurationModule[Profile]
    assert type(make_firewall_segment(layer=Profile)) is FirewallSegment[Profile]
    assert type(make_firewall_segment()) is FirewallSegment[Edge]
