"""Tests for resolution_error() and resolve_name() fuzzy matching helpers."""

import pytest
from pyvelo.config.resolver import resolution_error


class TestResolutionError:
    def test_close_match_suggests_candidates(self):
        err = resolution_error(
            "application",
            "Microsoft 365",
            ["Microsoft Office 365", "Google Workspace", "Zoom", "Slack"],
        )
        msg = str(err)
        assert "Unknown application: 'Microsoft 365'" in msg
        assert "Did you mean:" in msg
        assert "Microsoft Office 365" in msg

    def test_case_insensitive_matching(self):
        err = resolution_error(
            "application",
            "microsoft office 365",
            ["Microsoft Office 365", "Zoom", "Slack"],
        )
        msg = str(err)
        assert "Did you mean:" in msg
        assert "Microsoft Office 365" in msg

    def test_no_close_match_shows_available_list(self):
        err = resolution_error(
            "edge",
            "zzz-nonexistent",
            ["Branch-NYC", "Branch-London", "DC-Primary"],
        )
        msg = str(err)
        assert "Unknown edge: 'zzz-nonexistent'" in msg
        assert "Available:" in msg
        assert "Branch-NYC" in msg

    def test_no_close_match_truncates_long_list(self):
        names = [f"App-{i:03d}" for i in range(50)]
        err = resolution_error("application", "zzz-nonexistent", names)
        msg = str(err)
        assert "50 total, showing first 20" in msg
        # Should show first 20 sorted names
        assert "App-000" in msg
        assert "App-019" in msg
        # Should NOT show names beyond 20
        assert "App-020" not in msg

    def test_empty_available_list(self):
        err = resolution_error("segment", "Global", [])
        msg = str(err)
        assert "Unknown segment: 'Global'" in msg
        assert "Available: " in msg

    def test_returns_key_error(self):
        err = resolution_error("edge", "foo", ["bar"])
        assert isinstance(err, KeyError)

    def test_returns_structured_resolution_error(self):
        from pyvelo.config.resolver import ResolutionError

        err = resolution_error(
            "application", "Microsoft 36", ["Microsoft 365", "Slack"]
        )
        assert isinstance(err, ResolutionError)
        assert err.name == "Microsoft 36"
        assert err.identity_type == "application"
        assert "Microsoft 365" in err.candidates

    def test_resolution_error_caught_as_key_error(self):
        from pyvelo.config.resolver import ResolutionError

        err = resolution_error("edge", "foo", ["bar"])
        # Backwards compatibility: ResolutionError must be a KeyError subclass
        assert isinstance(err, ResolutionError)
        assert isinstance(err, KeyError)

    def test_multiple_suggestions_ranked(self):
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

    def test_exact_substring_match(self):
        err = resolution_error(
            "application",
            "Zoom Phone Call",
            ["Zoom", "Zoom Phone", "Microsoft Teams"],
        )
        msg = str(err)
        # "Zoom Phone Call" should be close to "Zoom Phone"
        assert "Did you mean:" in msg
        assert "Zoom Phone" in msg

    def test_custom_n_and_cutoff(self):
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

    def test_exact_match(self):
        from pyvelo.config.resolver import resolve_name

        name, val = resolve_name(
            "Branch-NYC", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge"
        )
        assert name == "Branch-NYC"
        assert val == 1

    def test_case_insensitive_match(self):
        from pyvelo.config.resolver import resolve_name

        name, val = resolve_name(
            "branch-nyc", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge"
        )
        assert name == "Branch-NYC"
        assert val == 1

    def test_case_insensitive_upper(self):
        from pyvelo.config.resolver import resolve_name

        name, val = resolve_name(
            "BRANCH-LAX", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge"
        )
        assert name == "Branch-LAX"
        assert val == 2

    def test_fuzzy_single_match_auto_resolves(self):
        from pyvelo.config.resolver import resolve_name

        name, val = resolve_name(
            "Example-Edg", {"Example-Edge": 1, "Branch-LAX": 2, "DC-Primary": 3}, "edge"
        )
        assert name == "Example-Edge"
        assert val == 1

    def test_fuzzy_ambiguous_raises(self):
        from pyvelo.config.resolver import resolve_name

        with pytest.raises(KeyError, match="Did you mean"):
            _ = resolve_name("Branch", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge")

    def test_no_match_raises(self):
        from pyvelo.config.resolver import resolve_name

        with pytest.raises(KeyError, match="Unknown edge"):
            _ = resolve_name("zzz-nothing", {"Branch-NYC": 1, "Branch-LAX": 2}, "edge")

    def test_exact_match_preferred_over_case_insensitive(self):
        from pyvelo.config.resolver import resolve_name

        # If both "foo" and "FOO" exist, exact match wins
        name, val = resolve_name("foo", {"foo": 1, "FOO": 2}, "edge")
        assert name == "foo"
        assert val == 1
