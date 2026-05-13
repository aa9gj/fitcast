#!/usr/bin/env python3
"""audit.py — re-run analysis on one job in verbose mode.

Surfaces Claude's full reasoning chain alongside the per-requirement evidence
so you can understand exactly why a particular score or verdict came out the
way it did, or spot-check where Claude may have misread your resume.

Reads from results.json (produced by pipeline.py).

Usage:
    python audit.py <job-url-from-results.json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

# Re-use the pipeline's schema, system prompt, and helpers — single source of truth.
from pipeline import (
    ANALYSIS_SCHEMA,
    JobAnalysis,
    MODEL,
    SYSTEM_INSTRUCTIONS,
)

ROOT = Path(__file__).parent
RESUME_PATH = ROOT / "resume.md"
RESULTS_JSON = ROOT / "results.json"


def _bar(label: str = "", width: int = 78) -> str:
    if not label:
        return "─" * width
    label = f" {label} "
    pad = max(0, width - len(label) - 4)
    left = "──"
    right = "─" * pad
    return f"\n{left}{label}{right}\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", help="Job URL from results.json (the `url` column)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable not set.")
    if not RESUME_PATH.exists():
        sys.exit(f"Error: {RESUME_PATH} not found.")
    if not RESULTS_JSON.exists():
        sys.exit(f"Error: {RESULTS_JSON} not found. Run `python pipeline.py` first.")

    resume = RESUME_PATH.read_text()
    try:
        results = json.loads(RESULTS_JSON.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"Error: {RESULTS_JSON} is not valid JSON: {exc}")

    target = next((r for r in results if r.get("url") == args.url), None)
    if not target:
        sys.exit(f"URL not found in {RESULTS_JSON.name}: {args.url}")

    posting_text = target.get("posting_text") or target.get("requirements_text", "")
    if not posting_text:
        sys.exit(
            "Error: no posting_text saved for this job. Re-run pipeline.py to capture it "
            "(older runs from before the schema update don't have full posting text saved)."
        )

    print(_bar(f"Auditing: {target['title']} @ {target['company']}"))
    print(f"Previous run:  {target.get('verdict', '?')}  ({target.get('score', '?')}/100)  "
          f"ATS {target.get('ats_score', '?')}/100")
    print(f"URL:           {target.get('url', '')}")

    client = anthropic.Anthropic()
    user_msg = (
        f"## Job: {target['title']} at {target['company']}\n"
        f"Location: {target.get('location', '')}\n\n"
        f"## Posting:\n{posting_text}"
    )

    print("\nRe-analyzing at higher effort... (this takes 20-40 seconds)\n", file=sys.stderr)

    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=[
            {"type": "text", "text": SYSTEM_INSTRUCTIONS},
            {"type": "text", "text": f"## Candidate Resume\n\n{resume}",
             "cache_control": {"type": "ephemeral"}},
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
        },
        messages=[{"role": "user", "content": user_msg}],
    )

    # Surface thinking blocks if visible. (On Sonnet 4.6 the text is visible
    # by default; on Opus 4.7 it's omitted unless display="summarized" is set.)
    for block in response.content:
        if block.type == "thinking":
            text = getattr(block, "thinking", "") or ""
            if text.strip():
                print(_bar("Claude's reasoning"))
                print(text)

    out = next((b.text for b in response.content if b.type == "text"), "")
    if not out:
        sys.exit("Error: empty response from Claude.")

    analysis = JobAnalysis.model_validate_json(out)
    qm = analysis.qualification_match

    print(_bar("Verdict"))
    print(f"  Qualification: {qm.verdict}  ({qm.score}/100)")
    print(f"  ATS keyword:   {analysis.ats_assessment.ats_score}/100")
    print(f"\n  Rationale: {qm.rationale}")

    print(_bar("Per-requirement evidence"))
    if not qm.requirements:
        print("  (No requirements identified.)")
    for r in qm.requirements:
        mark = "✓" if r.met else "✗"
        conf = {"high": "high  ", "medium": "medium", "low": "low   "}.get(r.confidence, r.confidence)
        print(f"  {mark} [{conf}] {r.requirement}")
        print(f"        evidence: {r.evidence}\n")

    print(_bar("ATS keyword analysis"))
    if analysis.ats_assessment.keyword_matches:
        print("  Matches (in resume):")
        for kw in analysis.ats_assessment.keyword_matches:
            print(f"    ✓ {kw}")
    if analysis.ats_assessment.keyword_gaps:
        print("\n  Gaps (NOT in resume — consider adding when you genuinely have the experience):")
        for kw in analysis.ats_assessment.keyword_gaps:
            print(f"    ✗ {kw}")
    if analysis.ats_assessment.format_warnings:
        print("\n  Format warnings:")
        for w in analysis.ats_assessment.format_warnings:
            print(f"    ! {w}")

    print(_bar("Requirements section (verbatim from posting)"))
    if analysis.requirements_section.found:
        heading = analysis.requirements_section.section_heading or "(no heading)"
        print(f"  Heading: {heading}\n")
        for line in analysis.requirements_section.text.split("\n"):
            print(f"  {line}")
    else:
        print("  (No clear requirements section found in posting.)")

    print()


if __name__ == "__main__":
    main()
