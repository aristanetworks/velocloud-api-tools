"""Tests for resolution_error() and resolve_name() fuzzy matching helpers."""

import copy
import pickle

import pytest
from pyvelo.config.resolver import resolve_name
from pyvelo.core.identity import (
    ApplicationIdentity,
    EdgeIdentity,
    HubIdentity,
    NetworkServiceIdentity,
    ResolutionError,
    SegmentIdentity,
    resolution_error,
)
from pyvelo.core.rebase import Path


def test_identity_type_is_a_class_and_the_candidates_are_the_names_listed() -> None:
    close = resolution_error(EdgeIdentity, "Branch-NY", ["Branch-NYC", "DC-Primary"])
    far = resolution_error(EdgeIdentity, "zzz", ["Branch-NYC", "DC-Primary"])

    assert close.identity_type is EdgeIdentity
    assert close.candidates == ["Branch-NYC"]
    assert str(far) == "Unknown edge: 'zzz'.\nAvailable: Branch-NYC, DC-Primary"
    assert far.candidates == ["Branch-NYC", "DC-Primary"]
    assert (far.path, far.wanted) == (None, None)


def test_the_candidates_are_at_most_the_twenty_names_the_message_shows() -> None:
    names = [f"edge-{n:02}" for n in range(30)]

    error = resolution_error(EdgeIdentity, "zzz", names)

    assert error.candidates == names[:20]
    assert "Available (30 total, showing first 20): edge-00, " in str(error)


def test_locate_sets_the_path_once_and_puts_it_before_the_message() -> None:
    error = resolution_error(SegmentIdentity, "Gest", ["Guest"])
    place = Path(("lan", "segment_id"))

    error.locate(place)
    error.locate(Path(("elsewhere",)))

    assert error.path == place
    assert str(error) == f"{place}: Unknown segment: 'Gest'.\nDid you mean:\n  - Guest"
    for copied in (copy.copy(error), pickle.loads(pickle.dumps(error))):
        assert (copied.path, copied.wanted, str(copied)) == (place, None, str(error))


def test_an_error_built_with_its_place_and_type_keeps_them() -> None:
    error = ResolutionError(
        name="radius-1",
        identity_type=NetworkServiceIdentity,
        candidates=["dns-a"],
        message="m",
        path=Path(("css", "provider")),
        wanted="dns",
    )

    assert (error.path, error.wanted) == (Path(("css", "provider")), "dns")
    error.locate(Path(("elsewhere",)))
    assert str(error) == "m"  # it had a place already


def test_the_default_message_names_the_identity_by_its_label() -> None:
    error = ResolutionError(name="x", identity_type=HubIdentity, candidates=[])

    assert str(error) == "Unknown hub: 'x'."


class TestResolutionError:
    def test_prints_as_its_message(self) -> None:
        err = resolution_error(EdgeIdentity, "Branch-NY", ["Branch-NYC", "DC-Primary"])
        assert str(err) == "Unknown edge: 'Branch-NY'.\nDid you mean:\n  - Branch-NYC"

    def test_close_match_suggests_candidates(self) -> None:
        err = resolution_error(
            ApplicationIdentity,
            "Microsoft 365",
            ["Microsoft Office 365", "Google Workspace", "Zoom", "Slack"],
        )
        msg = str(err)
        assert "Unknown application: 'Microsoft 365'" in msg
        assert "Did you mean:" in msg
        assert "Microsoft Office 365" in msg

    def test_case_insensitive_matching(self) -> None:
        err = resolution_error(
            ApplicationIdentity,
            "microsoft office 365",
            ["Microsoft Office 365", "Zoom", "Slack"],
        )
        msg = str(err)
        assert "Did you mean:" in msg
        assert "Microsoft Office 365" in msg

    def test_no_close_match_shows_available_list(self) -> None:
        err = resolution_error(
            EdgeIdentity,
            "zzz-nonexistent",
            ["Branch-NYC", "Branch-London", "DC-Primary"],
        )
        msg = str(err)
        assert "Unknown edge: 'zzz-nonexistent'" in msg
        assert "Available:" in msg
        assert "Branch-NYC" in msg

    def test_no_close_match_truncates_long_list(self) -> None:
        names = [f"App-{i:03d}" for i in range(50)]
        err = resolution_error(ApplicationIdentity, "zzz-nonexistent", names)
        msg = str(err)
        assert "50 total, showing first 20" in msg
        # Should show first 20 sorted names
        assert "App-000" in msg
        assert "App-019" in msg
        # Should NOT show names beyond 20
        assert "App-020" not in msg

    def test_empty_available_list(self) -> None:
        err = resolution_error(SegmentIdentity, "Global", [])
        msg = str(err)
        assert "Unknown segment: 'Global'" in msg
        assert "Available: " in msg

    def test_returns_key_error(self) -> None:
        err = resolution_error(EdgeIdentity, "foo", ["bar"])
        assert isinstance(err, KeyError)

    def test_returns_structured_resolution_error(self) -> None:
        err = resolution_error(
            ApplicationIdentity, "Microsoft 36", ["Microsoft 365", "Slack"]
        )
        assert isinstance(err, ResolutionError)
        assert err.name == "Microsoft 36"
        assert err.identity_type is ApplicationIdentity
        assert "Microsoft 365" in err.candidates

    def test_resolution_error_caught_as_key_error(self) -> None:
        err = resolution_error(EdgeIdentity, "foo", ["bar"])
        # Backwards compatibility: ResolutionError must be a KeyError subclass
        assert isinstance(err, ResolutionError)
        assert isinstance(err, KeyError)

    def test_multiple_suggestions_ranked(self) -> None:
        err = resolution_error(
            EdgeIdentity,
            "Branch-NY",
            ["Branch-NYC", "Branch-NYJ", "Branch-London", "DC-Primary"],
        )
        msg = str(err)
        assert "Did you mean:" in msg
        # Both NY variants should appear as suggestions
        assert "Branch-NYC" in msg
        assert "Branch-NYJ" in msg

    def test_exact_substring_match(self) -> None:
        err = resolution_error(
            ApplicationIdentity,
            "Zoom Phone Call",
            ["Zoom", "Zoom Phone", "Microsoft Teams"],
        )
        msg = str(err)
        # "Zoom Phone Call" should be close to "Zoom Phone"
        assert "Did you mean:" in msg
        assert "Zoom Phone" in msg

    def test_custom_n_and_cutoff(self) -> None:
        names = [f"Branch-{chr(65 + i)}" for i in range(10)]
        err = resolution_error(EdgeIdentity, "Branch-A", names, n=2, cutoff=0.5)
        msg = str(err)
        assert "Did you mean:" in msg
        # Should show at most 2 suggestions
        suggestions = [
            line.strip("- ").strip()
            for line in msg.split("\n")
            if line.strip().startswith("- ")
        ]
        assert len(suggestions) <= 2


class TestResolveName:
    """Tests for resolve_name() case-insensitive and fuzzy matching."""

    def test_exact_match(self) -> None:
        name, val = resolve_name(
            "Branch-NYC", {"Branch-NYC": 1, "Branch-LAX": 2}, EdgeIdentity
        )
        assert name == "Branch-NYC"
        assert val == 1

    def test_case_insensitive_match(self) -> None:
        name, val = resolve_name(
            "branch-nyc", {"Branch-NYC": 1, "Branch-LAX": 2}, EdgeIdentity
        )
        assert name == "Branch-NYC"
        assert val == 1

    def test_case_insensitive_upper(self) -> None:
        name, val = resolve_name(
            "BRANCH-LAX", {"Branch-NYC": 1, "Branch-LAX": 2}, EdgeIdentity
        )
        assert name == "Branch-LAX"
        assert val == 2

    def test_near_miss_raises_with_the_candidate(self) -> None:
        with pytest.raises(ResolutionError) as exc:
            _ = resolve_name(
                "Example-Edg",
                {"Example-Edge": 1, "Branch-LAX": 2, "DC-Primary": 3},
                EdgeIdentity,
            )
        assert exc.value.candidates == ["Example-Edge"]

    def test_fuzzy_is_opt_in(self) -> None:
        name, val = resolve_name(
            "Example-Edg",
            {"Example-Edge": 1, "Branch-LAX": 2},
            EdgeIdentity,
            fuzzy=True,
        )
        assert (name, val) == ("Example-Edge", 1)

    def test_fuzzy_ambiguous_raises(self) -> None:
        with pytest.raises(KeyError, match="Did you mean"):
            _ = resolve_name("Branch", {"Branch-NYC": 1, "Branch-LAX": 2}, EdgeIdentity)

    def test_no_match_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown edge"):
            _ = resolve_name(
                "zzz-nothing", {"Branch-NYC": 1, "Branch-LAX": 2}, EdgeIdentity
            )

    def test_exact_match_preferred_over_case_insensitive(self) -> None:
        # If both "foo" and "FOO" exist, exact match wins
        name, val = resolve_name("foo", {"foo": 1, "FOO": 2}, EdgeIdentity)
        assert name == "foo"
        assert val == 1
