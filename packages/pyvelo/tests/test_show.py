"""How a plan line and a write error show a value."""

import uuid
from typing import override

from pydantic import MISSING
from pyvelo.core.rebase import ItemMove, ItemPlace, Key
from pyvelo.core.show import moves_text, shown, shown_together, value_text


def test_value_text_is_json_with_sorted_keys():
    assert value_text({"b": 1, "a": [True, None]}) == '{"a": [true, null], "b": 1}'
    assert value_text(MISSING) == "(nothing)"


class Odd:
    @override
    def __repr__(self) -> str:
        return "<odd>"

    @override
    def __str__(self) -> str:
        raise RuntimeError("no text")


class Mute:
    @override
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


def test_value_text_never_raises():
    assert value_text(uuid.UUID(int=6)) == '"00000000-0000-0000-0000-000000000006"'
    assert value_text({1: "a", "b": 2}) == "{1: 'a', 'b': 2}"
    assert value_text(Odd()) == "<odd>"
    assert value_text(Mute()) == "<Mute>"
    cycle: list[object] = []
    cycle.append(cycle)
    assert value_text(cycle) == "[[...]]"
    assert shown(Odd()) == "<odd>"
    assert shown_together(MISSING, Odd(), uuid.UUID(int=6)) == (
        "(nothing)",
        "<odd>",
        '"00000000-0000-0000-0000-000000000006"',
    )


def test_a_value_within_the_width_is_shown_whole():
    assert shown("x" * 78) == '"' + "x" * 78 + '"'


def test_a_longer_value_is_cut_to_the_width():
    assert shown("x" * 200) == '"' + "x" * 79 + "…"


def hops(last_cost: int) -> list[dict[str, object]]:
    costs = [1, 2, 3, 4, 5, 6, 7, last_cost]
    return [{"ip": f"10.0.0.{n}", "cost": cost} for n, cost in enumerate(costs, 1)]


def test_short_values_are_shown_together_as_they_are():
    assert shown_together(1, MISSING, "x") == ("1", "(nothing)", '"x"')


def test_long_values_are_cut_where_they_first_differ():
    assert shown_together(hops(8), hops(99)) == (
        '…0.0.0.7"}, {"cost": 8, "ip": "10.0.0.8"}]',
        '…0.0.0.7"}, {"cost": 99, "ip": "10.0.0.8"}]',
    )


def test_a_short_value_beside_a_long_one_is_whole():
    assert shown_together(MISSING, "x" * 200, MISSING) == (
        "(nothing)",
        '"' + "x" * 79 + "…",
        "(nothing)",
    )


def test_long_values_that_are_the_same_are_cut_from_the_start():
    assert shown_together(hops(8), hops(8)) == (shown(hops(8)), shown(hops(8)))


def test_moves_text_names_five_moves_and_counts_the_rest():
    moves = [ItemMove(Key(f"r{n}"), ItemPlace(Key(f"r{n + 1}"))) for n in range(7)]
    assert moves_text(moves[:2]) == "r0 after r1; r1 after r2"
    assert moves_text([ItemMove(2, ItemPlace())]) == "#2 first"
    assert moves_text(moves) == (
        "r0 after r1; r1 after r2; r2 after r3; r3 after r4; r4 after r5; and 2 more"
    )
