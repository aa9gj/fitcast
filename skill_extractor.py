"""skill_extractor.py — deterministic skill extraction from text.

Loads the O*NET-derived skill catalog from data/skills.json (built by
scripts/bootstrap_ontologies.py) and provides a fast n-gram matcher.

Usage:
    from skill_extractor import get_extractor
    ext = get_extractor()
    skills_in_resume = ext.extract(resume_text)   # -> set[str] of canonical skill names
    skills_in_posting = ext.extract(posting_text)
    matched = skills_in_resume & skills_in_posting
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SKILLS_PATH = ROOT / "data" / "skills.json"


def _normalize(s: str) -> str:
    """Lowercase, strip parentheticals, collapse whitespace, drop trailing punctuation."""
    if not s:
        return ""
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = s.strip(".,;:")
    return s


def _tokenize(text: str) -> list[str]:
    """Split text into words. Treats most punctuation as word boundaries but
    preserves intra-word punctuation that occurs in tech terms (e.g., C++, .NET,
    Node.js, scikit-learn, GitHub Actions)."""
    text = text.lower()
    # Replace punctuation that's never part of a tech term with spaces
    text = re.sub(r"[^\w\s.+/#-]", " ", text)
    return text.split()


class SkillExtractor:
    def __init__(self) -> None:
        if not SKILLS_PATH.exists():
            raise FileNotFoundError(
                f"{SKILLS_PATH} not found. Run `python scripts/bootstrap_ontologies.py` first."
            )
        data = json.loads(SKILLS_PATH.read_text())
        self.metadata = {k: v for k, v in data.items() if k != "skills"}
        self.canonical_skills: list[str] = data["skills"]

        # Build lookup: normalized name -> canonical name.
        self._lookup: dict[str, str] = {}
        for s in self.canonical_skills:
            norm = _normalize(s)
            if not norm:
                continue
            # Prefer the entry with more capital letters (likely the brand-cased version).
            existing = self._lookup.get(norm)
            if existing is None or sum(c.isupper() for c in s) > sum(c.isupper() for c in existing):
                self._lookup[norm] = s

        # Pre-compute the max n-gram length needed (number of words in the longest skill).
        self._max_ngram = max(
            (len(_tokenize(s)) for s in self._lookup),
            default=1,
        )

    def extract(self, text: str) -> set[str]:
        """Return the canonical names of skills found in `text`.

        Uses n-gram lookup (1..max_ngram words at a time). Faster than
        per-skill regex scanning, deterministic, no LLM involvement.
        """
        if not text:
            return set()
        words = _tokenize(text)
        found: set[str] = set()
        n_max = self._max_ngram
        for i in range(len(words)):
            for n in range(1, min(n_max, len(words) - i) + 1):
                ngram = " ".join(words[i : i + n])
                ngram = _normalize(ngram)
                if ngram in self._lookup:
                    found.add(self._lookup[ngram])
        return found

    def info(self) -> str:
        return (
            f"{self.metadata.get('skill_count', '?')} skills "
            f"from {self.metadata.get('version', 'unknown')} "
            f"({self.metadata.get('onet_count', '?')} O*NET + "
            f"{self.metadata.get('supplement_count', '?')} supplement)"
        )


_extractor: SkillExtractor | None = None


def get_extractor() -> SkillExtractor:
    """Lazy singleton — first call loads + indexes skills.json."""
    global _extractor
    if _extractor is None:
        _extractor = SkillExtractor()
    return _extractor


# CLI for quick inspection: `python skill_extractor.py < some_text.txt`
if __name__ == "__main__":
    if not SKILLS_PATH.exists():
        sys.exit(f"Run `python scripts/bootstrap_ontologies.py` first to build {SKILLS_PATH}.")
    text = sys.stdin.read()
    if not text.strip():
        sys.exit("Pipe text in: `cat resume.md | python skill_extractor.py`")
    ext = get_extractor()
    print(f"Skill catalog: {ext.info()}", file=sys.stderr)
    print(f"\nText length: {len(text)} chars", file=sys.stderr)
    skills = ext.extract(text)
    print(f"Skills found: {len(skills)}\n", file=sys.stderr)
    for s in sorted(skills):
        print(f"  {s}")
