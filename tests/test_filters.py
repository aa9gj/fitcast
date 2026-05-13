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

    def test_no_min_threshold_passes(self):
        f = {"min_total_compensation": 0}
        assert matches_salary(self._job("Salary: $30,000."), f)

    def test_max_above_threshold_passes(self):
        f = {"min_total_compensation": 100000}
        # Range $120k–$180k → max is $180k → above $100k → pass
        assert matches_salary(self._job("Range: $120k - $180k."), f)

    def test_max_below_threshold_drops(self):
        f = {"min_total_compensation": 150000}
        # Single salary $100k → below $150k → drop
        assert not matches_salary(self._job("Salary: $100,000."), f)

    def test_no_salary_mentioned_passes(self):
        # When the posting doesn't disclose salary, we pass through
        # (don't penalize the company for non-disclosure)
        f = {"min_total_compensation": 150000}
        assert matches_salary(self._job("Salary: competitive."), f)
        assert matches_salary(self._job(""), f)

    def test_uses_max_of_range(self):
        # Range $90k–$120k against $100k threshold → max ($120k) above → pass
        f = {"min_total_compensation": 100000}
        assert matches_salary(self._job("Range: $90k - $120k."), f)

    def test_extracts_from_title(self):
        # Some postings have salary in the title (e.g., "Senior Engineer ($150k)")
        f = {"min_total_compensation": 100000}
        assert matches_salary(
            self._job(title="Senior Engineer ($150k)", content_html=""),
            f,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
