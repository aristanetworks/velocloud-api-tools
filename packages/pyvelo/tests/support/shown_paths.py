"""What a path pyvelo shows reaches, and whether it reads back as it was written.

Test modules import this as they import ``hub_state``
(``from shown_paths import reached, round_trips``).

A shown path names a field by its attribute and an item by the selector that
names it alone. ``reached`` follows one through data, and fails where a part
does not name exactly one thing; ``round_trips`` checks that the text of a path
parses back to the same parts.
"""

from pyvelo.core import Path, VcoModel, list_kind, select_items, selector_of
from pyvelo.core.base import IdentityResolver, is_dict, is_list
from pyvelo.core.owned import unwrap
from pyvelo.core.rebase import PathPart


def reached(
    data: VcoModel, path: Path, resolvers: IdentityResolver | None = None
) -> object:
    """The object or value at *path* of *data*.

    A field is read by attribute. The part after a list or a map names one of
    its items, as a patch's selector does (``select_items``): a key or a
    position by its selector text, a bare name as it is.

    Raises ``AssertionError``, naming the part, where a part selects no item or
    more than one, names no field, or stands where there is no model.
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
        fields = type(here).model_fields
        assert isinstance(part, str), (
            f"{_shown(part)} of {path}: an item part that follows no list or map"
        )
        assert part in fields, (
            f"{_shown(part)} of {path}: {type(here).__name__} has no such attribute"
        )
        if list_kind(fields[part]) is not None:
            owner, field = here, part
        here = unwrap(getattr(here, part))
    return here


def _item(
    owner: VcoModel,
    field: str,
    part: PathPart,
    path: Path,
    resolvers: IdentityResolver | None,
) -> object:
    """The one item of the list or map *field* of *owner* that *part* names."""
    selector = part if isinstance(part, str) else selector_of(part)
    positions = select_items(owner, field, selector, resolvers)
    assert len(positions) == 1, (
        f"{_shown(part)} of {path} selects {len(positions)} items of "
        f"{type(owner).__name__}.{field}, not one"
    )
    entries: object = unwrap(getattr(owner, field))
    if is_dict(entries):
        return list(entries.values())[positions[0]]
    assert is_list(entries), f"{type(owner).__name__}.{field} holds {entries!r}"
    return entries[positions[0]]


def _shown(part: PathPart) -> str:
    """*part* as a path writes it."""
    return str(Path((part,)))


def round_trips(path: Path) -> None:
    """Assert that the text of *path* parses back to the same path and parts."""
    parsed = Path.parse(str(path))
    assert parsed == path
    assert parsed.parts == path.parts
