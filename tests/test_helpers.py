"""Tests for the helper utilities in pipeline.py and skill_extractor.py."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import _parse_iso, html_to_text, matches_keywords
from skill_extractor import _normalize, _tokenize


# ───────────────────── HTML → text ─────────────────────

class TestHtmlToText:
    def test_simple_paragraphs(self):
        html = "<p>Hello.</p><p>World.</p>"
        assert "Hello." in html_to_text(html)
        assert "World." in html_to_text(html)

    def test_strips_tags(self):
        html = "<div><span>Plain text</span></div>"
        assert html_to_text(html) == "Plain text"

    def test_li_becomes_dash(self):
        html = "<ul><li>One</li><li>Two</li></ul>"
        result = html_to_text(html)
        assert "- One" in result
        assert "- Two" in result

    def test_collapses_whitespace(self):
        html = "<p>Lots    of   spaces</p>"
        assert "Lots of spaces" in html_to_text(html)

    def test_collapses_multiple_newlines(self):
        html = "<p>A</p><p></p><p></p><p>B</p>"
        text = html_to_text(html)
        # Should not have 3+ consecutive newlines
        assert "\n\n\n" not in text


# ───────────────────── ISO timestamp parsing ─────────────────────

class TestParseIso:
    def test_parses_z_suffix(self):
        dt = _parse_iso("2024-05-13T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2024

    def test_parses_offset(self):
        dt = _parse_iso("2024-05-13T10:30:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_naive_gets_utc(self):
        dt = _parse_iso("2024-05-13T10:30:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_handles_none(self):
        assert _parse_iso(None) is None

    def test_handles_empty(self):
        assert _parse_iso("") is None

    def test_handles_garbage(self):
        assert _parse_iso("not a date") is None


# ───────────────────── matches_keywords ─────────────────────

class TestMatchesKeywords:
    def _job(self, title: str, content_html: str = "") -> dict:
        return {"title": title, "content_html": content_html}

    def test_empty_keywords_matches_everything(self):
        assert matches_keywords(self._job("Anything"), [])

    def test_matches_in_title(self):
        assert matches_keywords(self._job("Senior Python Engineer"), ["python"])

    def test_body_keyword_ignored_under_default_title_scope(self):
        # Default scope is "title": a keyword that appears only in the body
        # must NOT match. (Body matching made a multi-keyword OR filter pass
        # ~everything, since JD boilerplate contains nearly any common term.)
        job = self._job("Engineer", "We need someone with regulatory experience.")
        assert not matches_keywords(job, ["regulatory"])

    def test_body_keyword_matches_under_explicit_body_scope(self):
        job = self._job("Engineer", "We need someone with regulatory experience.")
        assert matches_keywords(job, ["regulatory"], "title_and_body")

    def test_case_insensitive(self):
        assert matches_keywords(self._job("PYTHON DEVELOPER"), ["python"])
        assert matches_keywords(self._job("python developer"), ["PYTHON"])

    def test_no_match(self):
        assert not matches_keywords(self._job("Marketing Director"), ["python", "engineer"])

    def test_any_keyword_matches(self):
        # OR semantics: any one keyword present is enough
        job = self._job("Marketing Engineer", "")
        assert matches_keywords(job, ["python", "engineer"])

    def test_substring_match(self):
        # "regulator" is a substring of "regulatory"
        assert matches_keywords(self._job("Regulatory Specialist"), ["regulator"])


# ───────────────────── skill_extractor helpers ─────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert _normalize("Python") == "python"

    def test_strips_parentheticals(self):
        assert _normalize("Python (programming language)") == "python"

    def test_collapses_whitespace(self):
        assert _normalize("  Python    Programming  ") == "python programming"

    def test_strips_trailing_punctuation(self):
        assert _normalize("Python.") == "python"
        assert _normalize("Python,") == "python"

    def test_handles_empty(self):
        assert _normalize("") == ""
        assert _normalize(None) == ""


class TestTokenize:
    def test_basic_words(self):
        assert _tokenize("python sql java") == ["python", "sql", "java"]

    def test_lowercases(self):
        assert _tokenize("Python SQL") == ["python", "sql"]

    def test_preserves_intra_word_punctuation(self):
        # Tech terms commonly have these
        tokens = _tokenize("C++ scikit-learn Node.js .NET")
        assert "c++" in tokens
        assert "scikit-learn" in tokens
        assert "node.js" in tokens
        assert ".net" in tokens

    def test_treats_other_punct_as_separator(self):
        tokens = _tokenize("python, java; ruby:go")
        assert "python" in tokens
        assert "java" in tokens
        assert "ruby" in tokens
        assert "go" in tokens


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
