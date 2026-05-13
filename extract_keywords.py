#!/usr/bin/env python3
"""extract_keywords.py — derive search keywords from your resume.

Asks Claude to read resume.md and propose 10-20 high-signal substring
keywords for the pipeline's pre-filter. Output is YAML-formatted so you
can paste straight into config.yaml's `keywords:` block.

Cost: ~$0.005 per run.

Usage:
    python extract_keywords.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent
RESUME_PATH = ROOT / "resume.md"
MODEL = "claude-sonnet-4-6"

SYSTEM = """You are extracting search keywords from a candidate's resume for a job-board pipeline. The keywords will be used as a case-insensitive substring filter on job titles and descriptions, so they need to be words/phrases that postings would actually contain.

Output 10-20 high-signal keywords focused on:
- Specific technologies and tools (e.g., "Python", "GraphRAG", "Neo4j")
- Domain expertise (e.g., "regulatory", "claims substantiation", "computational biology")
- Role types the candidate could plausibly fit (e.g., "data scientist", "solutions architect")
- Industries (e.g., "biotech", "life sciences", "pharma")

Avoid:
- Generic words ("team", "develop", "manage", "lead")
- Things postings won't contain (degree titles, internal certifications, methodology jargon no posting uses)
- Pure acronyms when the spelled-out form is more searchable (or include both — e.g. "NLP", "natural language processing")

Output ONLY the keywords, one per line, lowercase. No bullets, no numbering, no commentary, no headings."""


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable not set.")
    if not RESUME_PATH.exists():
        sys.exit(f"Error: {RESUME_PATH} not found.")

    resume = RESUME_PATH.read_text().strip()
    if not resume:
        sys.exit(f"Error: {RESUME_PATH} is empty.")

    print(f"Reading {RESUME_PATH.name} and asking Claude for keyword suggestions...", file=sys.stderr)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=SYSTEM,
        messages=[{"role": "user", "content": resume}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    keywords: list[str] = []
    for line in text.split("\n"):
        kw = line.strip().lstrip("- ").strip().strip("\"'")
        if kw and not kw.startswith("#"):
            keywords.append(kw)

    if not keywords:
        sys.exit("Error: Claude returned no keywords. Check resume.md for content.")

    print(file=sys.stderr)
    print(f"# Extracted {len(keywords)} keyword(s) from {RESUME_PATH.name}.", file=sys.stderr)
    print("# Copy the block below into config.yaml (replacing the existing `keywords:` list).", file=sys.stderr)
    print("# Edit, prune, or extend as you see fit.", file=sys.stderr)
    print(file=sys.stderr)

    print("keywords:")
    for kw in keywords:
        print(f"  - {kw}")


if __name__ == "__main__":
    main()
