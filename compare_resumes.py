#!/usr/bin/env python3
"""compare_resumes.py — A/B test multiple resume versions against the same jobs.

Useful when you're iterating on resume wording: which framing scores best?
Pass two or more resume markdown files; for each job in results.json (or a
specific URL), run the analysis with each resume and report the comparison.

Usage:
    # Compare resume_v1.md vs resume_v2.md on the top 5 jobs from your last pipeline run:
    python compare_resumes.py --top 5 resume_v1.md resume_v2.md

    # Compare on one specific job:
    python compare_resumes.py --url <url-from-results.json> resume_v1.md resume_v2.md resume_v3.md

Cost: each (job × resume) pair is a deep-analysis call (~$0.025). So
3 jobs × 2 resumes = 6 calls = ~$0.15.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

from pipeline import (
    ANALYSIS_SCHEMA,
    JobAnalysis,
    MODEL,
    SYSTEM_INSTRUCTIONS,
    derive_ats_score,
    derive_qualification_score,
    html_to_text,
)
from skill_extractor import get_extractor

ROOT = Path(__file__).parent
RESULTS_JSON = ROOT / "results.json"


def _bar(label: str = "", width: int = 78) -> str:
    if not label:
        return "─" * width
    label = f" {label} "
    pad = max(0, width - len(label) - 4)
    return f"\n──{label}{'─' * pad}\n"


def analyze_with_resume(client, resume: str, posting_text: str, title: str, company: str, location: str):
    """One Claude call. Returns (qualification_score, verdict, ats_score, n_matched, n_total) or None on error."""
    user_msg = (
        f"## Job: {title} at {company}\n"
        f"Location: {location}\n\n"
        f"## Posting:\n{posting_text}"
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=[
                {"type": "text", "text": SYSTEM_INSTRUCTIONS},
                {"type": "text", "text": f"## Candidate Resume\n\n{resume}",
                 "cache_control": {"type": "ephemeral"}},
            ],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
            },
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.APIError as exc:
        print(f"    ! API error: {exc}", file=sys.stderr)
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    if not text:
        return None
    try:
        analysis = JobAnalysis.model_validate_json(text)
    except Exception as exc:
        print(f"    ! parse error: {exc}", file=sys.stderr)
        return None

    qm = analysis.qualification_match
    score, verdict, score_comp = derive_qualification_score(qm)

    extractor = get_extractor()
    resume_skills = extractor.extract(resume)
    ats_score, ats_comp, _, _ = derive_ats_score(resume_skills, posting_text, analysis.ats_assessment)

    return {
        "score": score,
        "verdict": verdict,
        "ats_score": ats_score,
        "n_met": score_comp["requirements_met"],
        "n_total": score_comp["requirements_total"],
        "skills_matched": ats_comp["skills_matched"],
        "skills_total": ats_comp["skills_total"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("resumes", nargs="+", help="Two or more resume.md files to compare")
    ap.add_argument("--top", type=int, default=None,
                    help="Use top N jobs from results.json")
    ap.add_argument("--url", default=None,
                    help="Specific job URL from results.json")
    args = ap.parse_args()

    if len(args.resumes) < 2:
        sys.exit("Error: provide at least 2 resume files to compare.")
    if not args.top and not args.url:
        sys.exit("Error: pass either --top N or --url <url>.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY not set.")
    if not RESULTS_JSON.exists():
        sys.exit(f"Error: {RESULTS_JSON} not found. Run pipeline.py first.")

    # Validate resume files exist + load them.
    resumes: list[tuple[str, str]] = []  # (label, content)
    for path_str in args.resumes:
        path = Path(path_str)
        if not path.exists():
            sys.exit(f"Error: resume file not found: {path}")
        resumes.append((path.name, path.read_text().strip()))

    # Pick target jobs from results.json.
    results = json.loads(RESULTS_JSON.read_text())
    if args.url:
        targets = [r for r in results if r.get("url") == args.url]
        if not targets:
            sys.exit(f"URL not found in {RESULTS_JSON.name}: {args.url}")
    else:
        targets = results[: args.top]
        if not targets:
            sys.exit("No results in results.json.")

    n_calls = len(targets) * len(resumes)
    est_cost = n_calls * 0.025
    print(f"Will run {len(targets)} job(s) × {len(resumes)} resume(s) = {n_calls} Claude calls "
          f"(~${est_cost:.2f}).\n", file=sys.stderr)

    client = anthropic.Anthropic()

    for job_idx, target in enumerate(targets, 1):
        title = target.get("title", "?")
        company = target.get("company", "?")
        location = target.get("location", "")
        posting_text = target.get("posting_text") or target.get("requirements_text", "")
        if not posting_text:
            print(f"[{job_idx}/{len(targets)}] {title} @ {company} — no posting_text saved, skipping",
                  file=sys.stderr)
            continue

        print(_bar(f"[{job_idx}/{len(targets)}] {title} @ {company}"))

        results_per_resume = []
        for resume_idx, (label, content) in enumerate(resumes, 1):
            print(f"  Analyzing with resume #{resume_idx}: {label}...", file=sys.stderr)
            r = analyze_with_resume(client, content, posting_text, title, company, location)
            if r is None:
                results_per_resume.append((label, None))
                continue
            results_per_resume.append((label, r))

        # Print comparison table.
        print(f"\n  {'Resume':<30} {'Score':>6}  {'Verdict':<14} {'ATS':>5}  {'Reqs':>10}  {'Skills':>10}")
        print(f"  {'-'*30} {'-'*6}  {'-'*14} {'-'*5}  {'-'*10}  {'-'*10}")
        for label, r in results_per_resume:
            if r is None:
                print(f"  {label:<30} {'?':>6}  {'(error)':<14}")
                continue
            print(f"  {label:<30} {r['score']:>6}  {r['verdict']:<14} {r['ats_score']:>5}  "
                  f"{r['n_met']}/{r['n_total']:<8}  {r['skills_matched']}/{r['skills_total']:<8}")

        # Pick winner.
        valid = [(l, r) for l, r in results_per_resume if r is not None]
        if valid:
            winner = max(valid, key=lambda lr: lr[1]["score"])
            print(f"\n  ★ Best: {winner[0]} ({winner[1]['score']}/100, {winner[1]['verdict']})")

    print()


if __name__ == "__main__":
    main()
