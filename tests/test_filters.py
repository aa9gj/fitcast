"""Tests for the location + salary filter functions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import (
    extract_salaries,
    matches_location,
    matches_salary,
    parse_time_window,
)


# ─────────────────────── matches_location ───────────────────────

class TestMatchesLocation:
    def _job(self, location: str) -> dict:
        return {"location": location}

    def test_no_filter_passes(self):
        assert matches_location(self._job("Anywhere"), None)
        assert matches_location(self._job("Anywhere"), {})

    def test_include_match_passes(self):
        f = {"include": ["remote", "san francisco"]}
        assert matches_location(self._job("Remote — US"), f)
        assert matches_location(self._job("San Francisco, CA"), f)

    def test_include_no_match_drops(self):
        f = {"include": ["remote"]}
        assert not matches_location(self._job("Berlin, Germany"), f)

    def test_exclude_match_drops(self):
        f = {"exclude": ["india", "philippines"]}
        assert not matches_location(self._job("Bangalore, India"), f)
        assert not matches_location(self._job("Manila, Philippines"), f)

    def test_exclude_no_match_passes(self):
        f = {"exclude": ["india"]}
        assert matches_location(self._job("Boston, MA"), f)

    def test_exclude_wins_over_include(self):
        # If location matches both include and exclude, exclude wins
        f = {"include": ["remote"], "exclude": ["philippines"]}
        assert not matches_location(self._job("Remote — Philippines"), f)
        # ...but pure include matches still work
        assert matches_location(self._job("Remote — Boston"), f)

    def test_case_insensitive(self):
        f = {"include": ["REMOTE"]}
        assert matches_location(self._job("remote — anywhere"), f)
        f = {"exclude": ["india"]}
        assert not matches_location(self._job("Bangalore, INDIA"), f)

    def test_empty_location_handles_gracefully(self):
        # Job with no location set
        f = {"include": ["remote"]}
        # No include match → fail
        assert not matches_location({"location": ""}, f)
        assert not matches_location({}, f)

    def test_include_empty_passes_through(self):
        # Empty include list = no inclusion requirement
        f = {"include": []}
        assert matches_location(self._job("anywhere"), f)

    # --- cities (word-boundary matching) ---

    def test_cities_word_boundary_avoids_substring_false_positive(self):
        # "MA" should NOT match inside "Manila"
        f = {"cities": ["MA"]}
        assert not matches_location(self._job("Manila, Philippines"), f)
        # ...but should match "Boston, MA"
        assert matches_location(self._job("Boston, MA"), f)

    def test_cities_match_passes(self):
        f = {"cities": ["Boston", "New York"]}
        assert matches_location(self._job("Boston, MA"), f)
        assert matches_location(self._job("New York, NY"), f)
        assert not matches_location(self._job("Seattle, WA"), f)

    def test_cities_or_include_either_passes(self):
        f = {"cities": ["Boston"], "include": ["remote"]}
        assert matches_location(self._job("Boston, MA"), f)
        assert matches_location(self._job("Remote — Anywhere"), f)
        assert not matches_location(self._job("Berlin, Germany"), f)

    def test_cities_exclude_still_wins(self):
        # Even if city matches, exclude wins
        f = {"cities": ["Boston"], "exclude": ["Boston Heights"]}
        assert not matches_location(self._job("Boston Heights, OH"), f)


# ─────────────────────── parse_time_window ───────────────────────

class TestParseTimeWindow:
    def test_none_returns_none(self):
        assert parse_time_window(None) is None

    def test_empty_string_returns_none(self):
        assert parse_time_window("") is None
        assert parse_time_window("   ") is None

    def test_plain_integer(self):
        assert parse_time_window(48) == 48
        assert parse_time_window(168) == 168

    def test_plain_integer_string(self):
        assert parse_time_window("48") == 48
        assert parse_time_window("168") == 168

    def test_hours_format(self):
        assert parse_time_window("24h") == 24
        assert parse_time_window("48h") == 48
        assert parse_time_window("168h") == 168

    def test_days_format(self):
        assert parse_time_window("1d") == 24
        assert parse_time_window("7d") == 168
        assert parse_time_window("30d") == 720

    def test_weeks_format(self):
        assert parse_time_window("1w") == 168
        assert parse_time_window("2w") == 336

    def test_composite_format(self):
        assert parse_time_window("1d12h") == 36
        assert parse_time_window("2w3d") == 408  # 2*168 + 3*24

    def test_uppercase_units(self):
        assert parse_time_window("24H") == 24
        assert parse_time_window("7D") == 168

    def test_whitespace_in_value(self):
        assert parse_time_window("1d 12h") == 36

    def test_invalid_returns_none(self):
        assert parse_time_window("garbage") is None
        assert parse_time_window("xyz") is None

    def test_zero_returns_none(self):
        # 0-hour window is meaningless; treat as disabled
        assert parse_time_window(0) is None
        assert parse_time_window("0") is None


# ─────────────────────── extract_salaries ───────────────────────

class TestExtractSalaries:
    def test_simple_dollar_amount(self):
        assert extract_salaries("Salary is $120,000 per year.") == [120000]

    def test_dollar_k_format(self):
        assert extract_salaries("Pay range $120k - $180k.") == [120000, 180000]
        assert extract_salaries("Up to $250K base.") == [250000]

    def test_range_extraction(self):
        text = "Compensation: $140,000 - $200,000 plus equity."
        result = extract_salaries(text)
        assert 140000 in result and 200000 in result

    def test_mixed_formats(self):
        text = "Base $150,000 with up to $50k bonus."
        result = extract_salaries(text)
        assert 150000 in result
        # $50k is below 30k floor — should NOT be in result
        # Wait, $50k IS above 30k, so it should be included
        assert 50000 in result

    def test_below_30k_filtered_out(self):
        # Very low numbers are likely not annual salaries (hourly, monthly, etc.)
        assert extract_salaries("Hourly rate: $25,000 cap.") == []
        assert extract_salaries("$15k stipend.") == []

    def test_above_2m_filtered_out(self):
        # Numbers above $2M aren't typical base salaries — usually total comp
        # or company-level figures (revenue, valuation).
        # $5,000,000 is filtered; $250,000 inside passes
        text = "Series A: $5,000,000 raised. Salary: $250,000."
        result = extract_salaries(text)
        assert 250000 in result
        assert 5000000 not in result

    def test_no_dollar_signs_no_matches(self):
        assert extract_salaries("Salary is competitive.") == []

    def test_empty_text_no_matches(self):
        assert extract_salaries("") == []

    def test_decimals_in_k_format(self):
        # "$150.5k" → 150500
        result = extract_salaries("Base: $150.5k.")
        assert 150500 in result


# ─────────────────────── matches_salary ───────────────────────

class TestMatchesSalary:
    def _job(self, content_html: str = "", title: str = "Engineer") -> dict:
        return {"title": title, "content_html": content_html}

    def test_no_filter_passes(self):
        assert matches_salary(self._job("Salary: $50,000."), None)
        assert matches_salary(self._job("Salary: $50,000."), {})

    def test_empty_bounds_passes(self):
        # Both bounds 0/None = no filtering
        f = {"min": 0, "max": 0}
        assert matches_salary(self._job("Salary: $30,000."), f)
        f = {"min": None, "max": None}
        assert matches_salary(self._job("Salary: $30,000."), f)

    # --- min only ---

    def test_min_only_above_passes(self):
        f = {"min": 100000}
        # Posting $120k–$180k → max $180k ≥ min $100k → pass
        assert matches_salary(self._job("Range: $120k - $180k."), f)

    def test_min_only_below_drops(self):
        f = {"min": 150000}
        # Posting $100k → max $100k < min $150k → drop
        assert not matches_salary(self._job("Salary: $100,000."), f)

    # --- max only ---

    def test_max_only_below_passes(self):
        f = {"max": 200000}
        # Posting $100k → min $100k ≤ max $200k → pass
        assert matches_salary(self._job("Salary: $100,000."), f)

    def test_max_only_above_drops(self):
        f = {"max": 200000}
        # Posting $250k → min $250k > max $200k → drop
        assert not matches_salary(self._job("Salary: $250,000."), f)

    # --- range (both min and max) ---

    def test_range_overlap_inside_passes(self):
        # Posting $120k–$180k fits cleanly inside $100k–$200k
        f = {"min": 100000, "max": 200000}
        assert matches_salary(self._job("Range: $120k - $180k."), f)

    def test_range_overlap_spans_passes(self):
        # Posting $80k–$300k spans the user's $100k–$200k range
        # Posting overlaps user range → pass (might still hit it)
        f = {"min": 100000, "max": 200000}
        assert matches_salary(self._job("Range: $80k - $300k."), f)

    def test_range_overlap_partial_low_passes(self):
        # Posting $50k–$120k overlaps the bottom of user range $100k–$200k
        f = {"min": 100000, "max": 200000}
        assert matches_salary(self._job("Range: $50k - $120k."), f)

    def test_range_overlap_partial_high_passes(self):
        # Posting $180k–$250k overlaps the top of user range $100k–$200k
        f = {"min": 100000, "max": 200000}
        assert matches_salary(self._job("Range: $180k - $250k."), f)

    def test_range_entirely_below_drops(self):
        # Posting $50k–$80k entirely below user range $100k–$200k
        f = {"min": 100000, "max": 200000}
        assert not matches_salary(self._job("Range: $50k - $80k."), f)

    def test_range_entirely_above_drops(self):
        # Posting $220k–$260k entirely above user range $100k–$200k
        f = {"min": 100000, "max": 200000}
        assert not matches_salary(self._job("Range: $220k - $260k."), f)

    # --- no salary mentioned ---

    def test_no_salary_mentioned_passes(self):
        # Non-disclosure should not penalize
        f = {"min": 150000, "max": 250000}
        assert matches_salary(self._job("Salary: competitive."), f)
        assert matches_salary(self._job(""), f)
        # Even when only min is set
        assert matches_salary(self._job("competitive"), {"min": 150000})

    # --- extraction sources ---

    def test_extracts_from_title(self):
        # Salary in title (e.g., "Senior Engineer ($150k)")
        f = {"min": 100000}
        assert matches_salary(
            self._job(title="Senior Engineer ($150k)", content_html=""), f
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
