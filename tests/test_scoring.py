"""Tests for the deterministic score-derivation functions in pipeline.py.

These functions are pure (no IO, no API calls) and are the most important
correctness boundary: every reported score in results.csv comes through here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import (
    ATSAssessment,
    QualificationMatch,
    RequirementBreakdown,
    RequirementEvidence,
    derive_ats_score,
    derive_qualification_score,
)


# ───────────────────── Helpers ─────────────────────

def _qm(
    requirements: list[tuple[str, bool]],
    *,
    years_required: int | None = None,
    years_resume_estimated: int | None = None,
    degree_required: str | None = None,
    degree_resume: str | None = None,
    degree_match: str = "unspecified",
) -> QualificationMatch:
    """Compact constructor for QualificationMatch."""
    reqs = [
        RequirementEvidence(
            requirement=name, met=met, confidence="high", evidence="test"
        )
        for name, met in requirements
    ]
    return QualificationMatch(
        requirements=reqs,
        breakdown=RequirementBreakdown(
            years_required=years_required,
            years_resume_estimated=years_resume_estimated,
            degree_required=degree_required,
            degree_resume=degree_resume,
            degree_match=degree_match,
        ),
        rationale="test",
    )


def _ats(matches: list[str], gaps: list[str], warnings: list[str] = None) -> ATSAssessment:
    return ATSAssessment(
        keyword_matches=matches,
        keyword_gaps=gaps,
        format_warnings=warnings or [],
    )


# ───────────────────── derive_qualification_score ─────────────────────

class TestQualificationScore:
    def test_all_met_no_penalties(self):
        qm = _qm([("a", True), ("b", True), ("c", True)])
        score, verdict, _ = derive_qualification_score(qm)
        assert score == 100
        assert verdict == "qualified"

    def test_none_met(self):
        qm = _qm([("a", False), ("b", False), ("c", False)])
        score, verdict, _ = derive_qualification_score(qm)
        assert score == 0
        assert verdict == "not_qualified"

    def test_qualified_threshold_at_80(self):
        # 4 of 5 met = 80% base, no penalties
        qm = _qm([("a", True)] * 4 + [("b", False)])
        score, verdict, _ = derive_qualification_score(qm)
        assert score == 80
        assert verdict == "qualified"

    def test_stretch_at_50(self):
        # 5 of 10 met = 50% base, no penalties
        qm = _qm([("x", True)] * 5 + [("y", False)] * 5)
        score, verdict, _ = derive_qualification_score(qm)
        assert score == 50
        assert verdict == "stretch"

    def test_not_qualified_below_50(self):
        # 4 of 10 met = 40% base, no penalties
        qm = _qm([("x", True)] * 4 + [("y", False)] * 6)
        score, verdict, _ = derive_qualification_score(qm)
        assert score == 40
        assert verdict == "not_qualified"

    def test_degree_below_penalty(self):
        # 9/10 met = 90 base, but degree below required = -30 → 60 → stretch
        qm = _qm(
            [("a", True)] * 9 + [("b", False)],
            degree_required="PhD",
            degree_resume="MS",
            degree_match="below",
        )
        score, verdict, comp = derive_qualification_score(qm)
        assert comp["degree_penalty"] == -30
        assert score == 60
        assert verdict == "stretch"

    def test_years_short_penalty(self):
        # 8/10 met = 80 base, 3 years short = -15 → 65 → stretch
        qm = _qm(
            [("a", True)] * 8 + [("b", False)] * 2,
            years_required=5,
            years_resume_estimated=2,
        )
        score, verdict, comp = derive_qualification_score(qm)
        assert comp["years_penalty"] == -15
        assert score == 65
        assert verdict == "stretch"

    def test_years_penalty_capped_at_30(self):
        # 10/10 met = 100, 10 years short would naively be -50 → capped at -30
        qm = _qm(
            [("a", True)] * 10,
            years_required=10,
            years_resume_estimated=0,
        )
        score, verdict, comp = derive_qualification_score(qm)
        assert comp["years_penalty"] == -30
        assert score == 70
        assert verdict == "stretch"

    def test_years_overqualified_no_penalty(self):
        # 10 yrs in resume vs 5 required → no penalty (negative gap clamps to 0)
        qm = _qm(
            [("a", True)] * 5,
            years_required=5,
            years_resume_estimated=10,
        )
        score, verdict, comp = derive_qualification_score(qm)
        assert comp["years_penalty"] == 0
        assert score == 100

    def test_combined_penalties(self):
        # 8/10 met = 80, -30 degree, -10 years (2 short) = 40 → not_qualified
        qm = _qm(
            [("a", True)] * 8 + [("b", False)] * 2,
            years_required=5,
            years_resume_estimated=3,
            degree_required="PhD",
            degree_resume="BS",
            degree_match="below",
        )
        score, verdict, comp = derive_qualification_score(qm)
        assert comp["degree_penalty"] == -30
        assert comp["years_penalty"] == -10
        assert score == 40
        assert verdict == "not_qualified"

    def test_score_clamps_to_zero(self):
        # 1/10 met = 10 base, -30 degree, -30 years = -50 → clamped to 0
        qm = _qm(
            [("a", True)] + [("b", False)] * 9,
            years_required=10,
            years_resume_estimated=2,
            degree_required="PhD",
            degree_resume="HS",
            degree_match="below",
        )
        score, verdict, _ = derive_qualification_score(qm)
        assert score == 0
        assert verdict == "not_qualified"

    def test_no_requirements_returns_neutral(self):
        qm = _qm([])
        score, verdict, comp = derive_qualification_score(qm)
        assert score == 50
        assert verdict == "stretch"
        assert comp["requirements_total"] == 0

    def test_components_dict_has_all_fields(self):
        qm = _qm([("a", True), ("b", False)])
        _, _, comp = derive_qualification_score(qm)
        for field in ["requirements_met", "requirements_total", "met_ratio",
                      "base_score", "degree_penalty", "years_penalty",
                      "adjustments_total"]:
            assert field in comp


# ───────────────────── derive_ats_score (hybrid) ─────────────────────

class TestATSScore:
    def test_perfect_overlap(self):
        """Resume has every skill the posting mentions."""
        resume_skills = {"Python", "SQL", "Docker"}
        ats = _ats(matches=["Python", "Docker"], gaps=[])
        score, comp, matched, missing = derive_ats_score(
            resume_skills, "We need Python SQL Docker for this role.", ats
        )
        # Onet: all 3 match. LLM: 2 match. Union still has all 3 in matched.
        assert "Python" in matched
        assert missing == []
        assert score == 100

    def test_no_overlap(self):
        """Resume has none of the skills."""
        resume_skills = set()
        ats = _ats(matches=[], gaps=["Java", "Spring", "Maven"])
        score, comp, matched, missing = derive_ats_score(
            resume_skills, "Java Spring Maven required.", ats
        )
        assert score == 0
        assert matched == []
        assert "Java" in missing or "Spring" in missing

    def test_no_skills_in_posting_returns_neutral(self):
        resume_skills = {"Python"}
        ats = _ats(matches=[], gaps=[])
        score, comp, matched, missing = derive_ats_score(resume_skills, "", ats)
        # Empty posting → no skills found → neutral 50.
        assert score == 50
        assert comp["skills_total"] == 0

    def test_format_warning_penalty(self):
        resume_skills = {"Python"}
        ats = _ats(matches=["Python"], gaps=["Java"], warnings=["table"])
        # Onet: Python+Java in posting; resume has Python (1 of 2 match).
        # LLM: matches=Python, gaps=Java (already counted via union).
        # Score = 1/2 * 100 - 5 (warning) = 45
        score, comp, _, _ = derive_ats_score(
            resume_skills, "Python and Java required.", ats
        )
        assert comp["format_warnings_penalty"] == -5
        assert score == 45

    def test_dedup_across_sources(self):
        """A skill appearing in both ontology AND LLM matches counts once."""
        resume_skills = {"Python"}
        ats = _ats(matches=["Python"], gaps=[])
        score, comp, matched, _ = derive_ats_score(
            resume_skills, "Python is required.", ats
        )
        # Python should appear in matched only once (deduped via .lower() compare)
        assert sum(1 for s in matched if s.lower() == "python") == 1

    def test_llm_adds_coverage_beyond_ontology(self):
        """LLM-extracted keywords not in O*NET still contribute."""
        resume_skills = {"Python"}  # what the ontology found in resume
        # Posting text mentions Python; LLM additionally identified a brand-new
        # tool not in O*NET.
        ats = _ats(matches=["BrandNewLLMTool"], gaps=[])
        score, comp, matched, _ = derive_ats_score(
            resume_skills, "Python required.", ats
        )
        # Onet match: Python (1). LLM match: BrandNewLLMTool (1). Total matched: 2.
        assert "Python" in matched
        assert "BrandNewLLMTool" in matched

    def test_components_dict_reports_source_breakdown(self):
        resume_skills = {"Python"}
        ats = _ats(matches=["Python"], gaps=["Foo"])
        _, comp, _, _ = derive_ats_score(
            resume_skills, "Python and Foo required.", ats
        )
        for field in ["onet_matched", "onet_missing", "llm_matched",
                      "llm_missing", "skills_matched", "skills_total",
                      "match_ratio", "methodology"]:
            assert field in comp


# ───────────────────── Run with: pytest tests/ ─────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
