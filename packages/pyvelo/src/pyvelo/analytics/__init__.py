"""VCO analytics as ``MetricFrame`` tables over Polars.

Needs the ``analytics`` extra (polars); importing the package without it raises
``ModuleNotFoundError``. ``Analytics`` holds the calls, and the enums and filter
schemas its parameters take are re-exported here.
"""

import importlib.util

if importlib.util.find_spec("polars") is None:
    raise ModuleNotFoundError(
        "pyvelo.analytics requires polars; "
        "install it with: pip install 'pyvelo[analytics]'",
        name="polars",
    )

from pyvelo.analytics.analytics import Analytics
from pyvelo.analytics.enums import (
    EnterpriseFirewallType,
    FirewallType,
    FlowGroupBy,
    FlowMetric,
)
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.edge import (
    ActivationState,
    EdgeServiceState,
    EdgeState,
    HaState,
)
from pyvelo.client.enums.event import EventSeverity, EventType
from pyvelo.client.enums.metrics import FilterFieldOperator, FlowFilterField
from pyvelo.client.schemas.event import (
    EnterpriseEventStringValueFilter,
    EventSorting,
)
from pyvelo.client.schemas.metrics import EnterpriseFlowMetricsFilter

__all__ = [
    "ActivationState",
    "Analytics",
    "EdgeServiceState",
    "EdgeState",
    "EnterpriseEventStringValueFilter",
    "EnterpriseFirewallType",
    "EnterpriseFlowMetricsFilter",
    "EventSeverity",
    "EventSorting",
    "EventType",
    "FilterFieldOperator",
    "FirewallType",
    "FlowFilterField",
    "FlowGroupBy",
    "FlowMetric",
    "HaState",
    "MetricFrame",
]
