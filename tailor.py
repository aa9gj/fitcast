#!/usr/bin/env python3
"""tailor.py — generate a tailored LaTeX resume + LaTeX cover letter for a job.

For each target job, writes three files into ./tailored/:
  <slug>_resume.tex   — tailored resume, compiles with `pdflatex`
  <slug>_cover.tex    — cover letter (uses Claude web search for recent
                        company news to anchor the opening paragraph)
  <slug>_changes.md   — audit of every material change made to the resume,
                        with the original-resume line that justifies it

Usage:
    python tailor.py --top 3
    python tailor.py <url>
    python tailor.py --top 5 --min-score 70
    python tailor.py --top 3 --no-cover     # tailored resume only
    python tailor.py <url> --no-resume      # cover letter only

Compile the .tex files with `pdflatex <file>.tex` (or `tectonic`, or upload
to Overleaf). Both documents are self-contained — no external .cls files.
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


# ─────────────────────── voice / deslop guidance ───────────────────────

DESLOP_RULES = """VOICE — read carefully, this is the most-violated rule:

Avoid AI-cliche openers. NEVER write:
  "I am excited to apply" / "I am writing to express my interest"
  "I am passionate about" / "I have always been passionate about"
  "Drawing on my experience" / "Leveraging my background"
  "As a [role] with N years of experience"
  "I would be thrilled to" / "I am eager to"

Avoid AI-cliche bullet/sentence structure:
  - No verb-front bullets like "Spearheaded", "Leveraged", "Empowered",
    "Orchestrated", "Pioneered", "Architected" as the first word.
  - No tricolons in every paragraph (X, Y, and Z).
  - No "not just X but Y" / "not only X but also Y" structures.
  - No rhetorical questions ("What does this mean for...?").
  - No "Furthermore", "Moreover", "Additionally" as sentence starters.
  - No hedge stacks: "absolutely critical", "incredibly significant",
    "deeply passionate", "uniquely positioned".
  - No "It's worth noting" / "It bears mentioning" / "Importantly".
  - No em-dash overuse — at most one per ~3 paragraphs.

Write like a competent human peer talking to another competent human:
  - Short, declarative sentences. Specific facts. Plain verbs.
  - Cite concrete projects, numbers, and outcomes from the resume.
  - If you don't have a specific fact for a claim, drop the claim — don't
    pad with vague-but-confident language.
"""


# ─────────────────────── LaTeX scaffolds ───────────────────────

LATEX_RESUME_GUIDE = r"""LATEX OUTPUT FORMAT — read carefully:

Produce a self-contained LaTeX document inside a ```latex code fence.
The document MUST compile cleanly with `pdflatex` out of the box.

Use ONLY this preamble (or a near-equivalent subset):

\documentclass[10pt,letterpaper]{article}
\usepackage[margin=0.7in]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}
\usepackage{parskip}
\pagestyle{empty}
\titleformat{\section}{\large\bfseries\MakeUppercase}{}{0em}{}[\titlerule]
\titlespacing*{\section}{0pt}{8pt}{4pt}
\setlist[itemize]{leftmargin=1.2em,itemsep=1pt,topsep=2pt}

Use ONLY these constructs in the body:
  \section{...}, \textbf{...}, \emph{...}, \textit{...}
  \begin{itemize} \item ... \end{itemize}
  \href{url}{text}, \hfill (for right-aligned dates)
  \\ for line breaks in headers

Do NOT use:
  - moderncv, awesome-cv, or any custom .cls
  - fontspec, xetex/xelatex-only packages
  - tikz, custom colors, custom fonts
  - tables (tabular) — use \hfill instead

Escape LaTeX special characters in user content:
  & → \&    % → \%    $ → \$    # → \#    _ → \_
  { → \{    } → \}    ~ → \textasciitilde{}    ^ → \textasciicircum{}
  \ → \textbackslash{}

Header pattern (name + contact on top):
  \begin{center}
  {\LARGE \textbf{Full Name}} \\[2pt]
  city, state $\cdot$ email $\cdot$ phone $\cdot$ \href{...}{linkedin}
  \end{center}
"""

LATEX_LETTER_GUIDE = r"""LATEX OUTPUT FORMAT — read carefully:

Produce a self-contained LaTeX cover letter inside a ```latex code fence.
The document MUST compile cleanly with `pdflatex` out of the box.

Use this exact preamble pattern:

\documentclass[11pt,letterpaper]{letter}
\usepackage[margin=1in]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage{parskip}

\signature{Full Name}
\address{Full Name \\ city, state \\ email \\ phone}

\begin{document}
\begin{letter}{Hiring Team \\ Company Name \\ (location optional)}
\opening{Dear Hiring Team,}

(body — 3 to 4 short paragraphs)

\closing{Sincerely,}
\end{letter}
\end{document}

Escape LaTeX special chars in user content (& → \&, % → \%, etc.).
Do NOT use tables, custom .cls files, or non-standard packages.
"""


# ─────────────────────── system prompts ───────────────────────

RESUME_SYSTEM = f"""You are tailoring a candidate's master resume for a specific job posting.

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

{DESLOP_RULES}

{LATEX_RESUME_GUIDE}

OUTPUT — TWO code fences in this order:
1. ```latex … ``` — the full tailored resume as a compilable .tex file
2. ```changes … ``` — a Markdown bulleted list. One bullet per material
   change, each citing the line in the master resume that justifies it.
   If you cannot honestly tailor without inventing experience, say so
   here instead of forcing it.

Output nothing else outside those two fences."""


COVER_SYSTEM = f"""You are writing a cover letter for a candidate applying to a specific job.

PROCESS:
1. Use the web_search tool (up to 3 queries) to find ONE recent, specific item about the company:
   - A product launch, new service, funding round, partnership, paper, or
     major announcement from the last ~6 months.
   - Skip generic "About Us" pages and old news.
   - Aim for a primary source (the company's own announcement) or a
     reputable trade outlet (TechCrunch, Endpoints News, BioPharma Dive,
     Fierce Biotech, etc., depending on the industry).
   - If the company is too obscure to find recent news, fall back to
     something specific from the job posting itself (a stated mission,
     a named product, a stated team focus). Don't fabricate news.
2. Pick ONE item — not a list. Specificity beats coverage.
3. Write the letter. Anchor the OPENING paragraph in that specific item
   and connect it concretely to the candidate's background. Do NOT
   summarize the company's mission or products in general terms.

LETTER STRUCTURE — 3 to 4 paragraphs, NOT longer:
- ¶1 (3-4 sentences): The specific recent thing + why it connects to
  the candidate's experience. Name the source if it's external press.
- ¶2 (3-5 sentences): Two or three concrete pieces of the candidate's
  experience that map to the posting's top requirements. Use specific
  projects, technologies, and outcomes from the resume — no generalities.
- ¶3 (2-3 sentences, optional): One thing the candidate is curious to
  learn or contribute on the team specifically.
- ¶closing (1-2 sentences): A direct closing — no "I would love to
  discuss further" boilerplate.

CONSTRAINTS:
- Do NOT invent experience or claims about the candidate.
- Do NOT invent claims about the company. If you couldn't verify
  something via search, don't say it.
- Length: ~250-350 words in the body. Short.

{DESLOP_RULES}

{LATEX_LETTER_GUIDE}

OUTPUT — exactly ONE ```latex … ``` code fence containing the full
compilable .tex file. Output nothing else outside that fence."""


# ─────────────────────── helpers ───────────────────────

def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:50]


def extract_fence(text: str, lang: str) -> str:
    """Extract content from the first ```<lang> ... ``` fence in `text`."""
    pattern = rf"```{lang}\s*\n(.*?)\n```"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def collect_text(response) -> str:
    """Concatenate all text blocks from an Anthropic response, in order.

    With server-side tool use (web_search), the response interleaves
    text blocks with server_tool_use / web_search_tool_result blocks.
    We only want the model's prose."""
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n\n".join(parts)


# ─────────────────────── per-job calls ───────────────────────

def tailor_resume(client: anthropic.Anthropic, resume: str, result: dict) -> tuple[str, str]:
    """Returns (resume_latex, changes_markdown). Either may be empty on failure."""
    posting = result.get("posting_text") or result.get("requirements_text", "")
    if not posting:
        return "", ""

    user_msg = f"""## Job Posting

Title: {result.get('title', '')}
Company: {result.get('company', '')}
Location: {result.get('location', 'N/A')}
URL: {result.get('url', '')}

{posting}

## Master Resume

{resume}

---

Now produce the two code fences as instructed: ```latex first, then ```changes.
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=RESUME_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user_msg}],
    )
    full = collect_text(response)
    return extract_fence(full, "latex"), extract_fence(full, "changes")


def write_cover_letter(client: anthropic.Anthropic, resume: str, result: dict) -> str:
    """Returns cover_latex (or empty string on failure)."""
    posting = result.get("posting_text") or result.get("requirements_text", "")
    if not posting:
        return ""

    user_msg = f"""## Job Posting

Title: {result.get('title', '')}
Company: {result.get('company', '')}
Location: {result.get('location', 'N/A')}
URL: {result.get('url', '')}

{posting}

## Candidate's Master Resume (use ONLY facts in here — do not invent)

{resume}

---

Process: search up to 3 times for ONE specific recent thing about
{result.get('company', 'the company')} (last ~6 months), pick the best one,
then write the cover letter as instructed. Output exactly one ```latex
fence with the full compilable .tex file.
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=COVER_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    full = collect_text(response)
    return extract_fence(full, "latex")


# ─────────────────────── main ───────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", nargs="?", help="Specific job URL from results.json")
    ap.add_argument("--top", type=int, default=None,
                    help="Tailor top N results by qualification score")
    ap.add_argument("--min-score", type=int, default=0,
                    help="Only tailor jobs with qualification score >= this (0-100)")
    ap.add_argument("--no-cover", action="store_true",
                    help="Skip cover letter generation (resume only)")
    ap.add_argument("--no-resume", action="store_true",
                    help="Skip resume tailoring (cover letter only)")
    args = ap.parse_args()

    if args.no_cover and args.no_resume:
        sys.exit("Error: --no-cover and --no-resume can't both be set.")
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
        targets = [r for r in results if r.get("score", 0) >= args.min_score][: args.top]
        if not targets:
            sys.exit(
                f"No results with score >= {args.min_score}. "
                f"Try lowering --min-score or rerunning pipeline.py."
            )

    client = anthropic.Anthropic()
    TAILORED_DIR.mkdir(exist_ok=True)

    do_resume = not args.no_resume
    do_cover = not args.no_cover
    parts = []
    if do_resume: parts.append("resume")
    if do_cover: parts.append("cover letter")
    label = " + ".join(parts)
    print(f"Tailoring {len(targets)} job(s) — generating {label} for each...\n",
          file=sys.stderr)

    for i, result in enumerate(targets, 1):
        title = result.get("title", "?")
        company = result.get("company", "?")
        score = result.get("score", "?")
        slug = f"{slugify(company)}_{slugify(title)}"
        print(f"[{i}/{len(targets)}] {title} @ {company} (score {score})",
              file=sys.stderr)

        ts = datetime.now(timezone.utc).isoformat()
        latex_header = (
            f"% Tailored for: {title} @ {company}\n"
            f"% Job URL: {result.get('url', '')}\n"
            f"% Generated: {ts}\n"
            f"% Qualification: {score}/100   ATS: {result.get('ats_score', '?')}/100\n"
            f"% Compile with: pdflatex {slug}_<resume|cover>.tex\n\n"
        )

        if do_resume:
            resume_tex, changes_md = tailor_resume(client, resume, result)
            if not resume_tex:
                print("    ! resume: empty / no posting text", file=sys.stderr)
            else:
                resume_path = TAILORED_DIR / f"{slug}_resume.tex"
                resume_path.write_text(latex_header + resume_tex + "\n")
                print(f"    resume  -> {resume_path.relative_to(ROOT)}", file=sys.stderr)
            if changes_md:
                changes_path = TAILORED_DIR / f"{slug}_changes.md"
                md_header = (
                    f"# Changes for {title} @ {company}\n\n"
                    f"- Job URL: {result.get('url', '')}\n"
                    f"- Generated: {ts}\n"
                    f"- Qualification: {score}/100, ATS: {result.get('ats_score', '?')}/100\n\n"
                    f"---\n\n"
                )
                changes_path.write_text(md_header + changes_md + "\n")
                print(f"    changes -> {changes_path.relative_to(ROOT)}", file=sys.stderr)

        if do_cover:
            cover_tex = write_cover_letter(client, resume, result)
            if not cover_tex:
                print("    ! cover: empty / no posting text", file=sys.stderr)
            else:
                cover_path = TAILORED_DIR / f"{slug}_cover.tex"
                cover_path.write_text(latex_header + cover_tex + "\n")
                print(f"    cover   -> {cover_path.relative_to(ROOT)}", file=sys.stderr)

    print(
        f"\nDone. Files in {TAILORED_DIR.relative_to(ROOT)}/.\n"
        f"Compile with:  pdflatex tailored/<file>.tex\n"
        f"Or use tectonic (`tectonic tailored/<file>.tex`) or upload to Overleaf.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
