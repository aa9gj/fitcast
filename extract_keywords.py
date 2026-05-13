#!/usr/bin/env python3
"""extract_keywords.py — derive search keywords from your resume.

Reads resume.md and asks Claude to:
1. Summarize your professional profile honestly (level, specializations, domain)
2. Identify role types you'd actually be qualified or stretch for
3. Generate substring keywords aligned to those target roles

Output is grouped so you can sanity-check Claude's read of your profile
before pasting the keywords into config.yaml.

Cost: ~$0.02 per run (uses adaptive thinking + medium effort).

Usage:
    python extract_keywords.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).parent
RESUME_PATH = ROOT / "resume.md"
MODEL = "claude-sonnet-4-6"


SYSTEM = """You are extracting search keywords for a job-board pipeline that uses case-insensitive substring matching on job titles + descriptions. The keyword filter is the FIRST gate — if a posting doesn't contain any of the keywords, it never reaches the more expensive analysis. Bad keywords mean either (a) too many irrelevant jobs survive and waste budget, or (b) genuinely good jobs get filtered out.

Process — think through each step before producing output:

1. READ THE RESUME CAREFULLY. Note honestly:
   - Years of industry (post-degree) experience
   - Degree level and field
   - Technical specializations (real depth, not just keywords mentioned in passing)
   - Domain expertise (industries / problem areas they've actually worked in)
   - Career level — be honest. A 2-3 year industry person is mid-level, NOT senior IC engineer. A scientific PhD is suited for "Scientist" / "Research" titles, not formal Engineering or PM titles unless they have direct experience in those tracks.

2. IDENTIFY SPECIFIC ROLE TYPES the candidate would be qualified (90+) or stretch (60-89) for. Be honest:
   - Don't list titles where the candidate is clearly underqualified (Director without 10+ yrs leadership, Senior Engineer without 5+ yrs eng, formal PM without PM experience)
   - DO list roles aligned to their actual specialization, even if niche
   - Prefer "Scientist" / "Researcher" / "Analyst" titles over "Engineer" titles for someone with a science PhD and short industry tenure
   - Include their current role-type AND adjacent ones they could plausibly pivot to

3. PRODUCE 15-25 KEYWORD SUBSTRINGS that:
   - Match the target role types from step 2 (e.g., "computational biolog" if you target Computational Biologist roles)
   - Are DISTINCTIVE — avoid generic high-frequency words like "data", "AI", "team", "develop", "analyze", "lead", "build"; these match every posting and defeat the filter
   - Include the candidate's specific tech stack and domain terms (e.g., "Neo4j", "GraphRAG", "regulatory", "multi-omics")
   - Use substring matching tactically — "computational biolog" catches both "biology" and "biologist"; "regulator" catches "regulatory" and "regulator"
   - Mix three categories roughly evenly: role types, technical specifics, and domain expertise

Be conservative on breadth. A keyword that matches too broadly is worse than one that matches narrowly — the deep-analysis stage filters further, but only on jobs that survive this first gate."""


SCHEMA = {
    "type": "object",
    "properties": {
        "profile_summary": {
            "type": "string",
            "description": "2-3 sentence honest summary of the candidate's profile, level, and key specializations.",
        },
        "target_roles": {
            "type": "array",
            "description": "Role types the candidate would be qualified or stretch for.",
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Specific role title (e.g., 'Senior Computational Biologist').",
                    },
                    "fit": {
                        "type": "string",
                        "enum": ["qualified", "stretch"],
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-line explanation of why this role fits (or where the stretch is).",
                    },
                },
                "required": ["role", "fit", "rationale"],
                "additionalProperties": False,
            },
        },
        "keywords": {
            "type": "array",
            "description": "15-25 distinctive substring keywords for the job-board filter.",
            "items": {"type": "string"},
        },
    },
    "required": ["profile_summary", "target_roles", "keywords"],
    "additionalProperties": False,
}


class TargetRole(BaseModel):
    role: str
    fit: Literal["qualified", "stretch"]
    rationale: str


class KeywordExtraction(BaseModel):
    profile_summary: str
    target_roles: list[TargetRole]
    keywords: list[str]


def _bar(label: str = "", width: int = 78) -> str:
    if not label:
        return "─" * width
    label = f" {label} "
    pad = max(0, width - len(label) - 4)
    return f"\n──{label}{'─' * pad}\n"


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable not set.")
    if not RESUME_PATH.exists():
        sys.exit(f"Error: {RESUME_PATH} not found.")

    resume = RESUME_PATH.read_text().strip()
    if not resume:
        sys.exit(f"Error: {RESUME_PATH} is empty.")

    print(f"Reading {RESUME_PATH.name} and reasoning about your profile...", file=sys.stderr)
    print("(uses adaptive thinking — takes 15-30 seconds, costs ~$0.02)", file=sys.stderr)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{"role": "user", "content": f"## Candidate Resume\n\n{resume}"}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        sys.exit("Error: empty response from Claude.")

    try:
        result = KeywordExtraction.model_validate_json(text)
    except (json.JSONDecodeError, ValidationError) as exc:
        sys.exit(f"Error: failed to parse Claude's response: {exc}")

    print(_bar("Your profile (as Claude reads it)"))
    print(result.profile_summary)

    print(_bar("Target roles you'd fit"))
    qualified = [r for r in result.target_roles if r.fit == "qualified"]
    stretch = [r for r in result.target_roles if r.fit == "stretch"]
    if qualified:
        print("Qualified (90+):")
        for r in qualified:
            print(f"  ✓ {r.role}")
            print(f"      → {r.rationale}")
    if stretch:
        print("\nStretch (60-89):")
        for r in stretch:
            print(f"  ~ {r.role}")
            print(f"      → {r.rationale}")

    print(_bar("Recommended keywords — paste this into config.yaml"))
    print()
    print("# Auto-extracted from resume.md by extract_keywords.py")
    print("# Edit, prune, or extend as you see fit.")
    print("keywords:")
    for kw in result.keywords:
        print(f"  - {kw}")
    print()


if __name__ == "__main__":
    main()
