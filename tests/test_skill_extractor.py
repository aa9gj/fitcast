"""Integration tests for the skill extractor — exercises the real skills.json."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skill_extractor import SKILLS_PATH, get_extractor


@pytest.fixture(scope="module")
def extractor():
    if not SKILLS_PATH.exists():
        pytest.skip(f"{SKILLS_PATH} not built — run `python bootstrap_ontologies.py`.")
    return get_extractor()


class TestSkillExtractor:
    def test_loads_thousands_of_skills(self, extractor):
        # Sanity: O*NET + supplement should produce a substantial catalog.
        assert len(extractor.canonical_skills) > 1000

    def test_extracts_python(self, extractor):
        skills = extractor.extract("We need a Python developer for our team.")
        assert any(s.lower() == "python" for s in skills)

    def test_extracts_multi_word_skill(self, extractor):
        skills = extractor.extract("Strong project management background required.")
        # Should match the multi-word phrase
        assert any("project management" in s.lower() for s in skills)

    def test_extracts_supplement_terms(self, extractor):
        # Terms in skills_supplement.txt that O*NET doesn't have
        skills = extractor.extract("Build a RAG system using GraphRAG and Neo4j.")
        skill_lower = {s.lower() for s in skills}
        assert "rag" in skill_lower
        assert "graphrag" in skill_lower
        assert "neo4j" in skill_lower

    def test_empty_text_returns_empty(self, extractor):
        assert extractor.extract("") == set()

    def test_irrelevant_text_returns_empty(self, extractor):
        # No tech / business / domain skills here
        skills = extractor.extract("The cat sat on the mat.")
        # May match "cat" or similar — most likely empty, but allow ≤2 matches
        assert len(skills) <= 2

    def test_case_insensitive_matching(self, extractor):
        upper = extractor.extract("PYTHON SQL DOCKER")
        lower = extractor.extract("python sql docker")
        assert upper == lower

    def test_punctuation_separates_tokens(self, extractor):
        # "Python, SQL, Docker" should match all three
        skills = extractor.extract("We use: Python, SQL, and Docker.")
        skill_lower = {s.lower() for s in skills}
        assert "python" in skill_lower
        assert "sql" in skill_lower
        assert "docker" in skill_lower

    def test_intra_word_punctuation_preserved(self, extractor):
        # Tech terms with internal punctuation should still match
        # (assuming they're in the catalog — node.js, scikit-learn)
        skills = extractor.extract("Frontend in node.js with scikit-learn for ML.")
        skill_lower = {s.lower() for s in skills}
        # At least one should be detected
        assert "node.js" in skill_lower or "scikit-learn" in skill_lower

    def test_deterministic_repeated_extraction(self, extractor):
        """Same text → same skill set, every time. The whole point of the
        ontology-based approach."""
        text = "Python developer with SQL, Docker, and Kubernetes experience."
        runs = [extractor.extract(text) for _ in range(5)]
        assert all(r == runs[0] for r in runs)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
