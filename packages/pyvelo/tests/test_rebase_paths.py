"""Path / Key: structured, unambiguous change locations."""

import copy
import pickle
import random

import pytest
from pyvelo.core.rebase import (
    Key,
    Path,
    PathPart,
    RawChange,
    find_lossy_assignments,
    raw_changes,
)

from .test_rebase import Root, seg

EXAMPLES: list[tuple[tuple[PathPart, ...], str]] = [
    ((), ""),
    (("a",), "a"),
    (("a", "b_2"), "a.b_2"),
    (("_x",), "_x"),
    (("segments", Key("A")), "segments[A]"),
    (("rules", Key("allow-all")), "rules[allow-all]"),
    (("rules", Key("_r1")), "rules[_r1]"),
    (("rules", Key("1st-rule")), "rules[1st-rule]"),
    (("segments", Key("Global Segment")), 'segments["Global Segment"]'),
    (("segments", Key("Corp.Guest v2"), "rules"), 'segments["Corp.Guest v2"].rules'),
    (("segments", Key("0")), 'segments["0"]'),
    (("segments", Key("007")), 'segments["007"]'),
    (("segments", Key(0)), "segments[0]"),
    (("segments", Key(123), "x"), "segments[123].x"),
    (("segments", Key(-5)), "segments[-5]"),
    (("rules", Key("a]b")), 'rules["a]b"]'),
    (("rules", Key("[")), 'rules["["]'),
    (("rules", Key("a,b")), 'rules["a,b"]'),
    (("rules", Key('q"uote')), 'rules["q\\"uote"]'),
    (("rules", Key("back\\slash")), 'rules["back\\\\slash"]'),
    (("rules", Key("two\nlines")), 'rules["two\\nlines"]'),
    (("rules", Key("")), 'rules[""]'),
    (("rules", Key("-x")), 'rules["-x"]'),
    (("rules", Key("é")), 'rules["\\u00e9"]'),
    (("segments", "weird.key"), 'segments."weird.key"'),
    (("segments", "weird.key", "x"), 'segments."weird.key".x'),
    (("a b",), '"a b"'),
    (("0abc",), '"0abc"'),
    (("",), '""'),
    (("", ""), '"".""'),
    (("a[1]",), '"a[1]"'),
    ((Key("A"),), "[A]"),
    ((Key("A"), "x"), "[A].x"),
    ((Key("A"), Key("B"), 3), "[A][B][#3]"),
    (("items", 0, "port"), "items[#0].port"),
    (("items", 12), "items[#12]"),
    (("rules", Key(True)), "rules[true]"),
    (("rules", Key(False)), "rules[false]"),
    (("rules", Key("true")), 'rules["true"]'),
    (("rules", Key("null")), "rules[null]"),
    (("rules", Key(1.5)), "rules[1.5]"),
    (("rules", Key(1.0)), "rules[1.0]"),
    (("rules", Key(-0.25)), "rules[-0.25]"),
    (("rules", Key(1e20)), "rules[1e+20]"),
    (("rules", Key(1e-07)), "rules[1e-07]"),
    (("rules", Key("1e-07")), 'rules["1e-07"]'),
    (("rules", Key("1.5")), 'rules["1.5"]'),
]


@pytest.mark.parametrize(("parts", "text"), EXAMPLES)
def test_rendering(parts: tuple[PathPart, ...], text: str):
    path = Path(parts)
    assert path == text
    assert str(path) == text
    assert path.parts == parts


@pytest.mark.parametrize(("parts", "text"), EXAMPLES)
def test_parse_is_the_inverse_of_rendering(parts: tuple[PathPart, ...], text: str):
    assert Path.parse(text).parts == parts
    assert Path.parse(str(Path(parts))).parts == parts


def _random_parts(rng: random.Random) -> tuple[PathPart, ...]:
    alphabet = [
        "a",
        "Z",
        "_",
        "0",
        "9",
        "-",
        " ",
        ".",
        "[",
        "]",
        '"',
        ",",
        "\\",
        "#",
        "é",
        "\n",
        "e",
        "+",
    ]
    scalars: list[str | int | float | bool] = [
        0,
        1,
        7,
        -3,
        10**30,
        1.5,
        -2.25,
        3.0,
        1e20,
        1e-07,
        True,
        False,
    ]

    def text() -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))

    parts: list[PathPart] = []
    for _ in range(rng.randint(0, 6)):
        match rng.randrange(4):
            case 0:
                parts.append(text())
            case 1:
                parts.append(Key(text()))
            case 2:
                parts.append(Key(rng.choice(scalars)))
            case _:
                parts.append(rng.randrange(0, 50))
    return tuple(parts)


@pytest.mark.parametrize("seed", range(5))
def test_random_paths_round_trip(seed: int):
    rng = random.Random(seed)
    for _ in range(300):
        parts = _random_parts(rng)
        path = Path(parts)
        assert Path.parse(str(path)).parts == parts, str(path)


def test_distinct_parts_render_distinctly():
    rng = random.Random(99)
    seen: dict[str, tuple[PathPart, ...]] = {}
    for _ in range(2000):
        parts = _random_parts(rng)
        text = str(Path(parts))
        assert seen.setdefault(text, parts) == parts, text


@pytest.mark.parametrize(
    "text",
    [
        "a.",
        ".a",
        "a..b",
        "a b",
        "a[1",
        "a[]",
        "a[#]",
        "a[#x]",
        "a[#-1]",
        'a["x"',
        'a["x"]]',
        'a["x]',
        '"unterminated',
        '"bad\\q"',
        "a[1]b",
        "a[1]]",
        "a.[1]",
        "1abc",
        "-a",
        "a[x y]",
        "a[ 1]",
        "a[007]",
        "a[+1]",
        "a[1.]",
        'a."x"y',
        "[",
        "]",
    ],
)
def test_parse_rejects_malformed_text(text: str):
    with pytest.raises(ValueError):
        _ = Path.parse(text)


def test_a_path_is_its_text_for_comparison_and_hashing():
    path = Path(("segments", Key("A"), "name"))
    assert path == "segments[A].name"
    assert hash(path) == hash("segments[A].name")
    lookup: dict[str, int] = {path: 1}
    assert lookup["segments[A].name"] == 1
    assert isinstance(path, str)


def test_child_appends_one_part():
    root = Path(())
    path = root.child("segments").child(Key("Global Segment")).child("rules").child(0)
    assert path == 'segments["Global Segment"].rules[#0]'
    assert path.parts == ("segments", Key("Global Segment"), "rules", 0)
    assert root.parts == ()


def test_a_str_is_not_a_sequence_of_parts():
    with pytest.raises(TypeError):
        _ = Path("segments")


def test_copy_and_pickle_keep_the_structure():
    path = Path(("segments", Key("Corp.Guest v2"), 3))
    for clone in (
        copy.copy(path),
        copy.deepcopy(path),
        pickle.loads(pickle.dumps(path)),
    ):
        assert isinstance(clone, Path)
        assert clone == path
        assert clone.parts == path.parts


def test_raw_change_accepts_plain_text_and_equals_a_produced_change():
    change = RawChange("segments[A].rules[r1].port", 1, 2)
    assert isinstance(change.path, Path)
    assert change.path.parts == ("segments", Key("A"), "rules", Key("r1"), "port")
    old = {"segments": [seg("A", 0, [{"name": "r1", "port": 1}])]}
    new = {"segments": [seg("A", 0, [{"name": "r1", "port": 2}])]}
    assert raw_changes(old, new, Root) == [change]


def test_raw_changes_address_a_key_with_special_characters():
    name = "Corp.Guest v2"
    old = {"segments": [seg(name, 0, [{"name": "r1", "port": 1}])]}
    new = {"segments": [seg(name, 0, [{"name": "r1", "port": 2}, {"name": "r2"}])]}
    changes = raw_changes(old, new, Root)
    assert [str(c.path) for c in changes] == [
        'segments["Corp.Guest v2"].rules[r1].port',
        'segments["Corp.Guest v2"].rules[r2]',
    ]
    assert changes[0].path.parts == (
        "segments",
        Key("Corp.Guest v2"),
        "rules",
        Key("r1"),
        "port",
    )
    assert changes[1].path.parts == ("segments", Key(name), "rules", Key("r2"))


def test_raw_changes_use_a_structured_name_for_odd_object_keys():
    (change,) = raw_changes({"a": {"x.y": 1}}, {"a": {"x.y": 2}})
    assert change.path == 'a."x.y"'
    assert change.path.parts == ("a", "x.y")


def test_lossy_assignments_address_a_key_with_special_characters():
    raw = {
        "segments": [
            seg("Corp.Guest v2", 0, [{"name": "r1"}]),
            seg("0", 1, [{"name": "r1"}]),
        ]
    }
    base, cur = Root.model_validate(raw), Root.model_validate(raw)
    cur.segments[0].rules[0].port = 0
    cur.segments[1].rules[0].port = 0
    paths = find_lossy_assignments(
        base,
        cur,
        base.model_dump(mode="json", by_alias=True),
        cur.model_dump(mode="json", by_alias=True),
    )
    assert paths == [
        'segments["Corp.Guest v2"].rules[r1].port',
        'segments["0"].rules[r1].port',
    ]
    assert paths[0].parts == (
        "segments",
        Key("Corp.Guest v2"),
        "rules",
        Key("r1"),
        "port",
    )
    assert paths[1].parts == ("segments", Key("0"), "rules", Key("r1"), "port")
