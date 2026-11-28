"""What a path pyvelo shows reaches, and whether it reads back as it was written.

Test modules import this as they import ``hub_state``
(``from shown_paths import reached, round_trips``).

A shown path names a field by its attribute and an item by the selector that
names it alone. ``reached`` follows one through data, and fails where a part
does not name exactly one thing; ``round_trips`` checks that the text of a path
parses back to the same parts.
"""

from pyvelo.core import Path, VcoModel, patch_key, select_items
from pyvelo.core.base import IdentityResolver, is_dict, is_list
from pyvelo.core.owned import unwrap
from pyvelo.core.rebase import PathPart


def reached(
    data: VcoModel, path: Path, resolvers: IdentityResolver | None = None
) -> object:
    """The object or value at *path* of *data*.

    A field is read by attribute. What it holds says how the next part is
    read: after a list or a map the part names one of its items, and after a
    model (also where the field could hold a list in its place) a field. A
    position part is the item at that position, whatever the keys of the
    others. A key and a bare name are read as a patch's selector is
    (``select_items``).

    This is the tests' own reading of a path, apart from the library's: it
    does not go through the patch key of a position, which a list may read as
    the key of another item.

    Raises ``AssertionError``, naming the part, where a part selects no item or
    more than one, is a position the list is too short for, names no field, or
    stands where there is no model.
    """
    here: object = data
    owner: VcoModel | None = None
    field = ""
    for part in path.parts:
        if owner is not None:
            here = _item(owner, field, part, path, resolvers)
            owner = None
            continue
        assert isinstance(here, VcoModel), (
            f"{_shown(part)} of {path}: there is no model there, but {here!r}"
        )
        assert isinstance(part, str), (
            f"{_shown(part)} of {path}: an item part that follows no list or map"
        )
        assert part in type(here).model_fields, (
            f"{_shown(part)} of {path}: {type(here).__name__} has no such attribute"
        )
        value = unwrap(getattr(here, part))
        if is_list(value) or is_dict(value):
            owner, field = here, part
        here = value
    return here


def _item(
    owner: VcoModel,
    field: str,
    part: PathPart,
    path: Path,
    resolvers: IdentityResolver | None,
) -> object:
    """The one item of the list or map *field* of *owner* that *part* names."""
    entries: object = unwrap(getattr(owner, field))
    place = f"{type(owner).__name__}.{field}"
    if isinstance(part, int):
        assert is_list(entries), f"{_shown(part)} of {path}: {place} is not a list"
        assert part < len(entries), (
            f"{_shown(part)} of {path}: {place} has {len(entries)} items"
        )
        return entries[part]
    positions = select_items(owner, field, patch_key(part), resolvers)
    assert len(positions) == 1, (
        f"{_shown(part)} of {path} selects {len(positions)} items of {place}, not one"
    )
    if is_dict(entries):
        return list(entries.values())[positions[0]]
    assert is_list(entries), f"{place} holds {entries!r}"
    return entries[positions[0]]


def _shown(part: PathPart) -> str:
    """*part* as a path writes it."""
    return str(Path((part,)))


def round_trips(path: Path) -> None:
    """Assert that the text of *path* parses back to the same path and parts."""
    parsed = Path.parse(str(path))
    assert parsed == path
    assert parsed.parts == path.parts
