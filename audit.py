#!/usr/bin/env python3
"""audit.py — re-run analysis on one job in verbose mode.

Surfaces Claude's full reasoning chain alongside the per-requirement evidence
AND the score derivation breakdown — so you can see exactly which numbers
went into the qualification + ATS scores.

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

# Re-use the pipeline's schema, system prompt, derivation, and helpers.
from pipeline import (
    ANALYSIS_SCHEMA,
    JobAnalysis,
    MODEL,
    SYSTEM_INSTRUCTIONS,
    compute_domain_fit,
    derive_ats_score,
    derive_qualification_score,
    get_embedding_model,
)

ROOT = Path(__file__).parent
RESUME_PATH = ROOT / "resume.md"
RESULTS_JSON = ROOT / "results.json"


def _bar(label: str = "", width: int = 78) -> str:
    if not label:
        return "─" * width
    label = f" {label} "
    pad = max(0, width - len(label) - 4)
    return f"\n──{label}{'─' * pad}\n"


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
        sys.exit(f"Error: {RESUME_PATH} not found. Run `python pipeline.py` first.")

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
            "(older runs from before the schema update don't have full posting text)."
        )

    print(_bar(f"Auditing: {target['title']} @ {target['company']}"))
    print(f"Previous run:  {target.get('verdict', '?')}  ({target.get('score', '?')}/100)  "
          f"ATS {target.get('ats_score', '?')}/100"
          + (f"  domain_fit {target.get('domain_fit_score', '?')}/100"
             if target.get('domain_fit_score') is not None else ""))
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

    # Surface thinking blocks if visible.
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
    ats = analysis.ats_assessment

    # Derive the same scores the pipeline does.
    score, verdict, score_components = derive_qualification_score(qm)
    ats_score, ats_components = derive_ats_score(ats)

    # Domain fit if available.
    domain_fit = None
    model = get_embedding_model()
    if model is not None:
        resume_embedding = model.encode(resume, convert_to_numpy=True)
        domain_fit = compute_domain_fit(resume_embedding, posting_text)

    print(_bar("Verdict (re-derived)"))
    print(f"  Qualification: {verdict}  ({score}/100)")
    print(f"  ATS keyword:   {ats_score}/100")
    if domain_fit is not None:
        print(f"  Domain fit:    {domain_fit}/100  (resume × posting embedding similarity)")
    print(f"\n  Rationale: {qm.rationale}")

    print(_bar("Qualification score derivation (the math)"))
    sc = score_components
    print(f"  Requirements met:        {sc['requirements_met']} of {sc['requirements_total']}  "
          f"(ratio {sc.get('met_ratio', '?')})")
    print(f"  Base score (ratio×100):  {sc['base_score']}")
    print(f"  Degree penalty:          {sc['degree_penalty']:+d}  "
          f"(required: {qm.breakdown.degree_required or 'unspecified'}, "
          f"resume: {qm.breakdown.degree_resume or 'unspecified'}, "
          f"match: {qm.breakdown.degree_match})")
    print(f"  Years penalty:           {sc['years_penalty']:+d}  "
          f"(required: {qm.breakdown.years_required or 'unspecified'}, "
          f"resume estimate: {qm.breakdown.years_resume_estimated or 'unspecified'})")
    print(f"  Adjustments total:       {sc['adjustments_total']:+d}")
    print(f"  Final score:             {score} → {verdict}")

    print(_bar("ATS score derivation"))
    ac = ats_components
    print(f"  Keywords matched:        {ac['keywords_matched']} of {ac['keywords_total']}  "
          f"(ratio {ac.get('match_ratio', '?')})")
    if ac.get("format_warnings_penalty"):
        print(f"  Format-warning penalty:  {ac['format_warnings_penalty']:+d}")
    print(f"  Final ATS score:         {ats_score}")

    print(_bar("Per-requirement evidence"))
    if not qm.requirements:
        print("  (No requirements identified.)")
    for r in qm.requirements:
        mark = "✓" if r.met else "✗"
        conf = {"high": "high  ", "medium": "medium", "low": "low   "}.get(r.confidence, r.confidence)
        print(f"  {mark} [{conf}] {r.requirement}")
        print(f"        evidence: {r.evidence}\n")

    print(_bar("ATS keyword analysis"))
    if ats.keyword_matches:
        print("  Matches (in resume):")
        for kw in ats.keyword_matches:
            print(f"    ✓ {kw}")
    if ats.keyword_gaps:
        print("\n  Gaps (NOT in resume — consider adding when you genuinely have the experience):")
        for kw in ats.keyword_gaps:
            print(f"    ✗ {kw}")
    if ats.format_warnings:
        print("\n  Format warnings:")
        for w in ats.format_warnings:
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
