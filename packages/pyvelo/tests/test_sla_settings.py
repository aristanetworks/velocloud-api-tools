"""SLA wire format: VCO UI uses lossPct, not loss_pct."""

from pyvelo.client.models.configuration.control_plane.root import GatewaySelection
from pyvelo.client.models.configuration.qos.rule import SlaSettings


class TestSlaSettingsWireFormat:
    def test_serializes_loss_pct_as_lossPct(self):
        sla = SlaSettings(latency_ms="0", jitter_ms="0", loss_percent="0.0")
        dumped = sla.model_dump(by_alias=True)
        assert dumped["lossPct"] == "0.0"
        assert "loss_pct" not in dumped
        assert dumped["latencyMs"] == "0"
        assert dumped["jitterMs"] == "0"

    def test_accepts_loss_pct_alias_on_validate_and_merge(self):
        sla = SlaSettings.model_validate({"loss_pct": "1.5"})
        assert sla.loss_percent == "1.5"

        sla.merge_patch({"loss_pct": "2.0"})
        assert sla.loss_percent == "2.0"

        sla.merge_patch({"lossPct": "3.0"})
        assert sla.loss_percent == "3.0"


class TestGatewaySelection:
    def test_secondary_accepts_null(self):
        gs = GatewaySelection.model_validate(
            {"primary": "100.64.65.2", "secondary": None, "mode": "dynamic"}
        )
        assert gs.secondary is None
        assert gs.primary == "100.64.65.2"
