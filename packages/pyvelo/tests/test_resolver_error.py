"""Tests for resolution_error() and resolve_name() fuzzy matching helpers."""

import pytest
from pyvelo.config.resolver import ResolutionError, resolution_error, resolve_name


class TestResolutionError:
    def test_prints_as_its_message(self) -> None:
        err = resolution_error("edge", "Branch-NY", ["Branch-NYC", "DC-Primary"])
        assert str(err) == "Unknown edge: 'Branch-NY'.\nDid you mean:\n  - Branch-NYC"

    def test_close_match_suggests_candidates(self) -> None:
        err = resolution_error(
            "application",
            "Microsoft 365",
            ["Microsoft Office 365", "Google Workspace", "Zoom", "Slack"],
        )
        msg = str(err)
        assert "Unknown application: 'Microsoft 365'" in msg
        assert "Did you mean:" in msg
        assert "Microsoft Office 365" in msg

    def test_case_insensitive_matching(self) -> None:
        err = resolution_error(
            "application",
            "microsoft office 365",
            ["Microsoft Office 365", "Zoom", "Slack"],
        )
        msg = str(err)
        assert "Did you mean:" in msg
        assert "Microsoft Office 365" in msg

    def test_no_close_match_shows_available_list(self) -> None:
        err = resolution_error(
            "edge",
            "zzz-nonexistent",
            ["Branch-NYC", "Branch-London", "DC-Primary"],
        )
        msg = str(err)
        assert "Unknown edge: 'zzz-nonexistent'" in msg
        assert "Available:" in msg
        assert "Branch-NYC" in msg

    def test_no_close_match_truncates_long_list(self) -> None:
        names = [f"App-{i:03d}" for i in range(50)]
        err = resolution_error("application", "zzz-nonexistent", names)
        msg = str(err)
        assert "50 total, showing first 20" in msg
        # Should show first 20 sorted names
        assert "App-000" in msg
        assert "App-019" in msg
        # Should NOT show names beyond 20
        assert "App-020" not in msg

    def test_empty_available_list(self) -> None:
        err = resolution_error("segment", "Global", [])
        msg = str(err)
        assert "Unknown segment: 'Global'" in msg
        assert "Available: " in msg

    def test_returns_key_error(self) -> None:
        err = resolution_error("edge", "foo", ["bar"])
        assert isinstance(err, KeyError)

    def test_returns_structured_resolution_error(self) -> None:
        err = resolution_error(
            "application", "Microsoft 36", ["Microsoft 365", "Slack"]
        )
        assert isinstance(err, ResolutionError)
        assert err.name == "Microsoft 36"
        assert err.identity_type == "application"
        assert "Microsoft 365" in err.candidates

    def test_resolution_error_caught_as_key_error(self) -> None:
        err = resolution_error("edge", "foo", ["bar"])
        # Backwards compatibility: ResolutionError must be a KeyError subclass
        assert isinstance(err, ResolutionError)
        assert isinstance(err, KeyError)

    def test_multiple_suggestions_ranked(self) -> None:
        err = resolution_error(
            "edge",
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
            "application",
            "Zoom Phone Call",
            ["Zoom", "Zoom Phone", "Microsoft Teams"],
        )
        msg = str(err)
        # "Zoom Phone Call" should be close to "Zoom Phone"
        assert "Did you mean:" in msg
        assert "Zoom Phone" in msg

    def test_custom_n_and_cutoff(self) -> None:
        names = [f"Branch-{chr(65 + i)}" for i in range(10)]
        err = resolution_error("edge", "Branch-A", names, n=2, cutoff=0.5)
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
            "Branch-NYC", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge"
        )
        assert name == "Branch-NYC"
        assert val == 1

    def test_case_insensitive_match(self) -> None:
        name, val = resolve_name(
            "branch-nyc", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge"
        )
        assert name == "Branch-NYC"
        assert val == 1

    def test_case_insensitive_upper(self) -> None:
        name, val = resolve_name(
            "BRANCH-LAX", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge"
        )
        assert name == "Branch-LAX"
        assert val == 2

    def test_near_miss_raises_with_the_candidate(self) -> None:
        with pytest.raises(ResolutionError) as exc:
            _ = resolve_name(
                "Example-Edg",
                {"Example-Edge": 1, "Branch-LAX": 2, "DC-Primary": 3},
                "edge",
            )
        assert exc.value.candidates == ["Example-Edge"]

    def test_fuzzy_is_opt_in(self) -> None:
        name, val = resolve_name(
            "Example-Edg", {"Example-Edge": 1, "Branch-LAX": 2}, "edge", fuzzy=True
        )
        assert (name, val) == ("Example-Edge", 1)

    def test_fuzzy_ambiguous_raises(self) -> None:
        with pytest.raises(KeyError, match="Did you mean"):
            _ = resolve_name("Branch", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge")

    def test_no_match_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown edge"):
            _ = resolve_name("zzz-nothing", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge")

    def test_exact_match_preferred_over_case_insensitive(self) -> None:
        # If both "foo" and "FOO" exist, exact match wins
        name, val = resolve_name("foo", {"foo": 1, "FOO": 2}, "edge")
        assert name == "foo"
        assert val == 1
