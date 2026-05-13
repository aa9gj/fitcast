#!/usr/bin/env python3
"""tailor.py — generate a tailored resume for a specific job.

Reads results.json (produced by pipeline.py), pulls the full posting text and
the candidate's master resume, and asks Claude to rewrite the resume so it
leads with the most relevant experience and uses the posting's vocabulary —
WHILE staying strictly truthful (no invented experience, no inflated years).

Usage:
    python tailor.py --top 3                     # tailor top 3 by qualification score
    python tailor.py <url>                       # tailor a specific job from results.json
    python tailor.py --top 5 --min-score 70      # only tailor matches above a score floor
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent
RESUME_PATH = ROOT / "resume.md"
RESULTS_JSON = ROOT / "results.json"
TAILORED_DIR = ROOT / "tailored"

MODEL = "claude-sonnet-4-6"


SYSTEM_INSTRUCTIONS = """You are tailoring a candidate's master resume for a specific job posting.

YOUR JOB: rewrite the resume so it leads with the most relevant experience and uses the posting's exact vocabulary where the candidate genuinely has that experience.

YOU MUST NOT:
- Invent skills, technologies, or experience the candidate doesn't have
- Inflate years of experience or scope of responsibility
- Add credentials, degrees, certifications, or employers they don't have
- Change factual details (dates, employers, titles, locations, degree fields)
- Fabricate metrics or achievements

YOU MAY:
- Reorder bullets within each role to lead with the most relevant
- Rephrase bullets to mirror the posting's vocabulary, ONLY when the candidate's actual experience matches that vocabulary
- Drop bullets that are clearly irrelevant to this specific job (don't drop entire roles)
- Tighten the professional summary to focus on this role's needs
- Surface quantified achievements that directly align with the posting's requirements
- Reorder the skills section to lead with what the posting mentions
- Adjust section ordering (e.g., put a relevant project section higher)

OUTPUT FORMAT:
1. The full tailored resume in clean Markdown — keep it the same length as the original (don't pad).
2. A `## Changes Summary` section at the bottom listing each material change you made and the specific line in the candidate's resume that justifies it.

If you cannot honestly tailor the resume to this job (e.g., the candidate isn't qualified at all and tailoring would require inventing experience), say so explicitly in the Changes Summary instead of forcing it."""


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:50]


def tailor_one(client: anthropic.Anthropic, resume: str, result: dict) -> str:
    posting = result.get("posting_text") or result.get("requirements_text", "")
    if not posting:
        return ""

    user_msg = f"""## Job Posting

Title: {result.get('title', '')}
Company: {result.get('company', '')}
Location: {result.get('location', 'N/A')}
URL: {result.get('url', '')}

{posting}

## Master Resume

{resume}

---

Now produce the tailored resume in Markdown, followed by a `## Changes Summary` section.
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_INSTRUCTIONS,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user_msg}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", nargs="?", help="Specific job URL from results.json")
    ap.add_argument("--top", type=int, default=None,
                    help="Tailor top N results by qualification score")
    ap.add_argument("--min-score", type=int, default=0,
                    help="Only tailor jobs with qualification score >= this (0-100)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable not set.")
    if not RESUME_PATH.exists():
        sys.exit(f"Error: {RESUME_PATH} not found.")
    if not RESULTS_JSON.exists():
        sys.exit(
            f"Error: {RESULTS_JSON} not found. Run `python pipeline.py` first to "
            "generate it."
        )
    if not (args.url or args.top):
        sys.exit("Error: provide either a job URL or --top N. See --help.")

    resume = RESUME_PATH.read_text().strip()
    try:
        results = json.loads(RESULTS_JSON.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"Error: {RESULTS_JSON} is not valid JSON: {exc}")

    if args.url:
        targets = [r for r in results if r.get("url") == args.url]
        if not targets:
            sys.exit(f"URL not found in {RESULTS_JSON.name}: {args.url}")
    else:
        # results.json is sorted by score desc.
        targets = [r for r in results if r.get("score", 0) >= args.min_score][: args.top]
        if not targets:
            sys.exit(
                f"No results with score >= {args.min_score}. "
                f"Try lowering --min-score or rerunning pipeline.py."
            )

    client = anthropic.Anthropic()
    TAILORED_DIR.mkdir(exist_ok=True)

    print(f"Tailoring {len(targets)} resume(s)...\n", file=sys.stderr)

    for i, result in enumerate(targets, 1):
        title = result.get("title", "?")
        company = result.get("company", "?")
        score = result.get("score", "?")
        print(
            f"[{i}/{len(targets)}] {title} @ {company} (score {score})",
            file=sys.stderr,
        )

        markdown = tailor_one(client, resume, result)
        if not markdown:
            print("    ! empty response or no posting text", file=sys.stderr)
            continue

        filename = f"{slugify(company)}_{slugify(title)}.md"
        path = TAILORED_DIR / filename
        header = (
            f"<!--\n"
            f"Tailored resume for: {title} @ {company}\n"
            f"Job URL: {result.get('url', '')}\n"
            f"Generated: {datetime.now(timezone.utc).isoformat()}\n"
            f"Qualification score: {score}/100  ATS score: {result.get('ats_score', '?')}/100\n"
            f"-->\n\n"
        )
        path.write_text(header + markdown)
        print(f"    -> {path.relative_to(ROOT)}", file=sys.stderr)

    print(
        f"\nDone. Tailored files in {TAILORED_DIR.relative_to(ROOT)}/.\n"
        f"To convert any file to DOCX (for ATS upload):\n"
        f"    pandoc {TAILORED_DIR.relative_to(ROOT)}/<file>.md -o <file>.docx",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
