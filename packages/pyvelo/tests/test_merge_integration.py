"""Integration tests for merge_patch combining all features."""

from typing import Annotated

from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import EdgeLocal, KeyField, OverrideFlag


class SegmentMeta(VcoModel):
    name: str
    segment_id: int = 0


class Rule(VcoModel):
    name: str
    enabled: bool = True
    priority: int = 0


class Segment(VcoModel):
    segment: SegmentMeta
    override: Annotated[bool, OverrideFlag] = False
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)
    cidr_ip: Annotated[str, EdgeLocal] = ""


class Config(VcoModel):
    segments: Annotated[list[Segment], KeyField("segment.name")] = Field(
        default_factory=list
    )
    enabled: bool = True


class TestFullMergeScenario:
    def _make_config(self) -> Config:
        return Config(
            segments=[
                Segment(
                    segment=SegmentMeta(name="Global", segment_id=0),
                    rules=[
                        Rule(name="allow-all", priority=999),
                        Rule(name="block-ssh", priority=1),
                    ],
                ),
                Segment(
                    segment=SegmentMeta(name="PCI", segment_id=1),
                    rules=[Rule(name="deny-all")],
                ),
            ]
        )

    def test_nested_keyed_merge(self):
        """Merge a rule update into a specific segment by name."""
        m = self._make_config()
        m.merge_patch(
            {"segments": {"Global": {"rules": {"block-ssh": {"priority": 0}}}}}
        )
        global_seg = m.segments[0]
        ssh_rule = next(r for r in global_seg.rules if r.name == "block-ssh")
        assert ssh_rule.priority == 0
        # Override triggered because rules change is not EdgeLocal
        assert global_seg.override is True

    def test_add_rule_to_segment(self):
        """Add a new rule to an existing segment."""
        m = self._make_config()
        m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "new-rule": {
                                "name": "new-rule",
                                "priority": 5,
                                "enabled": True,
                            }
                        }
                    }
                }
            }
        )
        global_seg = m.segments[0]
        assert len(global_seg.rules) == 3
        assert global_seg.rules[2].name == "new-rule"

    def test_delete_rule_from_segment(self):
        """Delete a rule from a segment."""
        m = self._make_config()
        m.merge_patch({"segments": {"Global": {"rules": {"block-ssh": None}}}})
        global_seg = m.segments[0]
        assert len(global_seg.rules) == 1
        assert global_seg.rules[0].name == "allow-all"

    def test_edge_local_change_no_override(self):
        """EdgeLocal field change doesn't trigger override."""
        m = self._make_config()
        m.merge_patch({"segments": {"Global": {"cidr_ip": "10.0.0.1"}}})
        assert m.segments[0].override is False

    def test_mixed_edge_local_and_normal_triggers_override(self):
        """Mixed EdgeLocal + normal change triggers override."""
        m = self._make_config()
        m.merge_patch(
            {
                "segments": {
                    "Global": {"cidr_ip": "10.0.0.1", "rules": {"new": {"name": "new"}}}
                }
            }
        )
        assert m.segments[0].override is True

    def test_pci_segment_unchanged(self):
        """Unmentioned segments are untouched."""
        m = self._make_config()
        m.merge_patch(
            {"segments": {"Global": {"rules": {"block-ssh": {"priority": 0}}}}}
        )
        pci = m.segments[1]
        assert pci.segment.name == "PCI"
        assert len(pci.rules) == 1
        assert pci.override is False
