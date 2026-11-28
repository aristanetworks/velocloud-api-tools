"""``raw_changes`` records how it paired each keyed list (``PairedList``)."""

from typing import Annotated

from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.rebase import Key, PairedList, Path, raw_changes


class Rule(VcoModel):
    name: str
    rule_id: str | None = None
    port: int = 0


class Seg(VcoModel):
    name: str
    rules: Annotated[list[Rule], KeyField("name", alt="rule_id")] = Field(
        default_factory=list
    )


class Root(VcoModel):
    segments: Annotated[list[Seg], KeyField("name")] = Field(default_factory=list)
    plain: list[Rule] = Field(default_factory=list)


def rule(name: str, rule_id: str | None = None, port: int = 0) -> dict[str, object]:
    row: dict[str, object] = {"name": name, "port": port}
    if rule_id is not None:
        row["ruleId"] = rule_id
    return row


def root(*rules: dict[str, object]) -> dict[str, object]:
    """Two segments: ``G`` has *rules*, and ``H`` one rule that never changes."""
    return {
        "segments": [
            {"name": "G", "rules": list(rules)},
            {"name": "H", "rules": [rule("x", "9")]},
        ]
    }


def test_each_list_changed_item_by_item_is_recorded_by_its_path() -> None:
    """``a`` is renamed and goes last, ``b`` is removed, and ``new`` is added."""
    old = root(rule("a", "1"), rule("b", "2"), rule("c", "3"))
    new = root(rule("c", "3", port=1), rule("new"), rule("a2", "1"))
    lists: dict[Path, PairedList] = {}
    changes = raw_changes(old, new, Root, lists=lists)
    assert changes == raw_changes(old, new, Root)
    # A list comes before the lists inside its items; one that is the same has none.
    assert list(lists) == ["segments", "segments[G].rules"]
    both = (Key("G"), Key("H"))
    assert lists[Path(("segments",))] == PairedList(((0, 0), (1, 1)), both, both, both)
    assert lists[Path.parse("segments[G].rules")] == PairedList(
        pairs=((2, 0), (0, 2)),
        old_parts=(Key("a"), Key("b"), Key("c")),
        names=(Key("c"), Key("new"), Key("a")),
        new_parts=(Key("c"), Key("new"), Key("a2")),
    )


def test_an_item_whose_key_is_not_its_alone_is_recorded_by_its_position() -> None:
    old = root(rule("a", "1"), rule("a", "2"))
    new = root(rule("a", "2"), rule("a", "1"), rule("a"))
    lists: dict[Path, PairedList] = {}
    _ = raw_changes(old, new, Root, lists=lists)
    assert lists[Path.parse("segments[G].rules")] == PairedList(
        pairs=((1, 0), (0, 1)), old_parts=(0, 1), names=(1, 0, 2), new_parts=(0, 1, 2)
    )


def test_a_list_that_is_not_changed_item_by_item_is_not_recorded() -> None:
    """A list that cannot be paired, a list without a key, and equal documents."""
    old = root(rule("a"), rule("a"))
    old["plain"] = [rule("p")]
    new = root(rule("a"), rule("a", port=5))
    new["plain"] = [rule("q")]
    lists: dict[Path, PairedList] = {}
    changes = raw_changes(old, new, Root, lists=lists)
    assert [str(change.path) for change in changes] == ["segments[G].rules", "plain"]
    assert list(lists) == ["segments"]
    same: dict[Path, PairedList] = {}
    assert raw_changes(old, old, Root, lists=same) == []
    assert same == {}
