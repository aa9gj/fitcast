#!/usr/bin/env python3
"""Job hunting pipeline: scrape Greenhouse boards and The Muse, locate the
requirements section in each posting, predict whether the candidate qualifies,
and write ranked results to CSV + JSON.

Usage:
    pip install -r requirements.txt
    cp resume.example.md resume.md && $EDITOR resume.md
    export ANTHROPIC_API_KEY=sk-ant-...
    python pipeline.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import anthropic
import requests
import yaml
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
RESUME_PATH = ROOT / "resume.md"
RESULTS_CSV = ROOT / "results.csv"
RESULTS_JSON = ROOT / "results.json"
APPLIED_PATH = ROOT / "applied.json"
BOOTSTRAP_PATH = ROOT / "companies.bootstrap.yaml"

# Sonnet 4.6 is the default for deep analysis — structured extraction +
# ranked match against a resume is well within its capabilities, at ~40%
# the cost of Opus 4.7. For more nuance change to "claude-opus-4-7".
MODEL = "claude-sonnet-4-6"

# Haiku 4.5 is used for the optional cheap pre-rank pass. ~$0.0007 per call.
PRERANK_MODEL = "claude-haiku-4-5"

GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"
MUSE_BASE = "https://www.themuse.com/api/public/jobs"


# ───────────────────────────── Output schema ────────────────────────────────

class RequirementsSection(BaseModel):
    found: bool
    section_heading: str | None = None
    text: str = ""


class RequirementEvidence(BaseModel):
    requirement: str
    met: bool
    confidence: Literal["high", "medium", "low"]
    evidence: str


class QualificationMatch(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Literal["qualified", "stretch", "not_qualified"]
    requirements: list[RequirementEvidence]
    rationale: str


class ATSAssessment(BaseModel):
    ats_score: int = Field(ge=0, le=100)
    keyword_matches: list[str]
    keyword_gaps: list[str]
    format_warnings: list[str]


class JobAnalysis(BaseModel):
    requirements_section: RequirementsSection
    qualification_match: QualificationMatch
    ats_assessment: ATSAssessment


ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "requirements_section": {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "section_heading": {"type": ["string", "null"]},
                "text": {"type": "string", "description": "Verbatim quote of the requirements section."},
            },
            "required": ["found", "section_heading", "text"],
            "additionalProperties": False,
        },
        "qualification_match": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "description": "0-100. 90+ qualified, 60-89 stretch, <60 not qualified."},
                "verdict": {"type": "string", "enum": ["qualified", "stretch", "not_qualified"]},
                "requirements": {
                    "type": "array",
                    "description": "One entry per requirement found in the posting's requirements section.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "requirement": {
                                "type": "string",
                                "description": "Short paraphrase of the requirement as stated in the posting.",
                            },
                            "met": {
                                "type": "boolean",
                                "description": "Whether the candidate's resume clearly demonstrates this requirement.",
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "high = clear evidence in resume, medium = inferred, low = ambiguous.",
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Specific quote/reference from the resume supporting the assessment, or 'not mentioned in resume' / 'resume shows X yrs vs N+ required' for unmet ones.",
                            },
                        },
                        "required": ["requirement", "met", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                },
                "rationale": {"type": "string", "description": "2-3 sentence honest explanation."},
            },
            "required": ["score", "verdict", "requirements", "rationale"],
            "additionalProperties": False,
        },
        "ats_assessment": {
            "type": "object",
            "properties": {
                "ats_score": {"type": "integer", "description": "0-100 keyword/format alignment score."},
                "keyword_matches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Important keywords from the posting that appear in the resume.",
                },
                "keyword_gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Important keywords from the posting that are missing from the resume.",
                },
                "format_warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Resume format issues that could trip up an ATS parser. Empty if none.",
                },
            },
            "required": ["ats_score", "keyword_matches", "keyword_gaps", "format_warnings"],
            "additionalProperties": False,
        },
    },
    "required": ["requirements_section", "qualification_match", "ats_assessment"],
    "additionalProperties": False,
}


# ─────────────────────────── HTML → text ────────────────────────────────────

class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("p", "br", "div"):
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")


def html_to_text(html: str) -> str:
    parser = _HTMLStripper()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ─────────────────────────── Timestamp helper ───────────────────────────────

def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ─────────────────────────── Source: Greenhouse ─────────────────────────────

def fetch_greenhouse_jobs(slug: str) -> list[dict]:
    url = f"{GREENHOUSE_BASE}/{slug}/jobs"
    try:
        resp = requests.get(url, params={"content": "true"}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status != 404:
            print(f"  ! greenhouse/{slug}: {exc}", file=sys.stderr)
        return []
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "source": "greenhouse",
            "id": str(j.get("id", "")),
            "title": j.get("title", ""),
            "company": slug,
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "content_html": j.get("content", "") or "",
            "posted_at": _parse_iso(j.get("updated_at")),
        })
    return jobs


# ─────────────────────────── Source: The Muse ───────────────────────────────

def fetch_muse_jobs(
    categories: list[str],
    levels: list[str],
    locations: list[str],
    max_pages: int = 2,
) -> list[dict]:
    jobs: list[dict] = []
    for page in range(max_pages):
        params: list[tuple[str, str]] = [
            ("page", str(page)),
            ("descending", "true"),
        ]
        for cat in categories:
            params.append(("category", cat))
        for lvl in levels:
            params.append(("level", lvl))
        for loc in locations:
            params.append(("location", loc))

        try:
            resp = requests.get(MUSE_BASE, params=params, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  ! themuse: {exc}", file=sys.stderr)
            break

        data = resp.json()
        for item in data.get("results", []):
            company = (item.get("company") or {}).get("name", "")
            location_names = [
                (loc or {}).get("name", "") for loc in (item.get("locations") or [])
            ]
            jobs.append({
                "source": "themuse",
                "id": str(item.get("id", "")),
                "title": item.get("name", ""),
                "company": company,
                "location": ", ".join(filter(None, location_names)),
                "url": (item.get("refs") or {}).get("landing_page", ""),
                "content_html": item.get("contents", "") or "",
                "posted_at": _parse_iso(item.get("publication_date")),
            })

        if page + 1 >= data.get("page_count", 0):
            break

    return jobs


# ───────────────────────────── State files ──────────────────────────────────

def load_applied_urls() -> set[str]:
    if not APPLIED_PATH.exists():
        return set()
    try:
        return set(json.loads(APPLIED_PATH.read_text()).keys())
    except (json.JSONDecodeError, OSError):
        return set()


def load_bootstrap_companies() -> list[str]:
    if not BOOTSTRAP_PATH.exists():
        return []
    try:
        data = yaml.safe_load(BOOTSTRAP_PATH.read_text()) or {}
        return data.get("greenhouse_companies") or []
    except (yaml.YAMLError, OSError):
        return []


# ─────────────────────────── Pre-rank with Haiku ────────────────────────────

PRERANK_PROMPT_TEMPLATE = """Resume (summary):
{resume_summary}

Job: {title} at {company}
Posting (start):
{snippet}

On a 0-10 scale, how relevant is this job to this candidate's actual background and skills?
- 0-3: clearly unrelated
- 4-6: tangentially related, might consider
- 7-10: clearly relevant

Respond with ONLY the integer score, nothing else."""


def make_resume_summary(resume: str, max_chars: int = 1500) -> str:
    if len(resume) <= max_chars:
        return resume
    truncated = resume[:max_chars]
    last_break = truncated.rfind("\n\n")
    if last_break > max_chars // 2:
        return truncated[:last_break]
    return truncated


def prerank_score_one(client: anthropic.Anthropic, resume_summary: str, job: dict) -> int:
    snippet = html_to_text(job.get("content_html", ""))[:800]
    if not snippet:
        return 0
    prompt = PRERANK_PROMPT_TEMPLATE.format(
        resume_summary=resume_summary,
        title=job.get("title", ""),
        company=job.get("company", ""),
        snippet=snippet,
    )
    try:
        resp = client.messages.create(
            model=PRERANK_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError:
        return 5  # default to medium on transient errors
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    m = re.search(r"\d+", text)
    if not m:
        return 5
    return max(0, min(10, int(m.group(0))))


def prerank_jobs(
    client: anthropic.Anthropic,
    resume: str,
    jobs: list[dict],
    threshold: int,
    max_candidates: int,
    max_workers: int = 10,
) -> list[dict]:
    if not jobs:
        return jobs
    pool = jobs
    if len(pool) > max_candidates:
        random.seed(42)
        pool = random.sample(pool, max_candidates)
        print(
            f"Pre-ranking pool capped at {max_candidates} (sampled from {len(jobs)})",
            file=sys.stderr,
        )

    summary = make_resume_summary(resume)
    print(f"Pre-ranking {len(pool)} jobs with {PRERANK_MODEL}...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        scores = list(ex.map(lambda j: prerank_score_one(client, summary, j), pool))
    for job, score in zip(pool, scores):
        job["prerank_score"] = score

    above = [j for j in pool if j["prerank_score"] >= threshold]
    above.sort(key=lambda j: j["prerank_score"], reverse=True)
    print(
        f"  -> {len(above)} of {len(pool)} pre-ranked above threshold {threshold}",
        file=sys.stderr,
    )
    return above


# ───────────────────────────── Scrape orchestrator ──────────────────────────

def matches_keywords(job: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = (job["title"] + " " + job["content_html"]).lower()
    return any(kw.lower() in haystack for kw in keywords)


def scrape_jobs(config: dict, client: anthropic.Anthropic, resume: str) -> list[dict]:
    keywords = config.get("keywords") or []
    max_jobs = int(config.get("max_jobs", 10))
    posted_within_hours = config.get("posted_within_hours")
    all_jobs: list[dict] = []

    # --- Greenhouse: hand-picked + bootstrap-expanded, fetched in parallel.
    gh_companies = ((config.get("greenhouse") or {}).get("companies")) or []
    bootstrap = load_bootstrap_companies()
    gh_companies_all = list(dict.fromkeys(list(gh_companies) + list(bootstrap)))

    if gh_companies_all:
        print(
            f"Scraping {len(gh_companies_all)} Greenhouse boards "
            f"({len(gh_companies)} configured + {len(bootstrap)} bootstrapped) "
            f"in parallel...",
            file=sys.stderr,
        )
        with ThreadPoolExecutor(max_workers=10) as ex:
            for jobs in ex.map(fetch_greenhouse_jobs, gh_companies_all):
                all_jobs.extend(jobs)
        print(
            f"  greenhouse: {sum(1 for j in all_jobs if j['source'] == 'greenhouse')} jobs",
            file=sys.stderr,
        )

    # --- The Muse.
    muse_cfg = config.get("muse")
    if muse_cfg:
        print("Querying The Muse...", file=sys.stderr)
        muse_jobs = fetch_muse_jobs(
            categories=muse_cfg.get("categories") or [],
            levels=muse_cfg.get("levels") or [],
            locations=muse_cfg.get("locations") or [],
            max_pages=int(muse_cfg.get("max_pages", 2)),
        )
        print(f"  themuse: {len(muse_jobs)} jobs", file=sys.stderr)
        all_jobs.extend(muse_jobs)

    fetched = len(all_jobs)

    # --- Keyword filter.
    filtered = [j for j in all_jobs if matches_keywords(j, keywords)]

    # --- Date filter (drop jobs with no timestamp; better safe than wrong).
    if posted_within_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=int(posted_within_hours))
        before = len(filtered)
        filtered = [j for j in filtered if j.get("posted_at") and j["posted_at"] >= cutoff]
        print(
            f"  date filter (last {posted_within_hours}h): {before} -> {len(filtered)}",
            file=sys.stderr,
        )

    # --- Skip already-applied jobs.
    applied_urls = load_applied_urls()
    if applied_urls:
        before = len(filtered)
        filtered = [j for j in filtered if (j.get("url") or "") not in applied_urls]
        print(
            f"  skipping {before - len(filtered)} already-tracked jobs",
            file=sys.stderr,
        )

    # --- Dedupe by URL.
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for j in filtered:
        url = j.get("url") or ""
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(j)

    print(
        f"Pool: {fetched} fetched -> {len(filtered)} after filters -> "
        f"{len(deduped)} after dedup",
        file=sys.stderr,
    )

    # --- Optional cheap Haiku pre-rank pass before expensive deep analysis.
    prerank_cfg = config.get("prerank") or {}
    if prerank_cfg.get("enabled", False) and len(deduped) > max_jobs:
        deduped = prerank_jobs(
            client=client,
            resume=resume,
            jobs=deduped,
            threshold=int(prerank_cfg.get("threshold", 5)),
            max_candidates=int(prerank_cfg.get("max_candidates", 100)),
        )
        # When pre-rank is on, the top max_jobs by prerank_score are deep-analyzed.
        return deduped[:max_jobs]

    # No pre-rank: deterministic shuffle so each run picks a varied sample.
    random.seed(42)
    random.shuffle(deduped)
    return deduped[:max_jobs]


# ──────────────────────────── Claude analysis ───────────────────────────────

SYSTEM_INSTRUCTIONS = """You are a careful job-application analyst. For each job posting:

1. Find the section that lists minimum requirements / qualifications / what the candidate must have. Common headings include "Requirements", "Minimum Qualifications", "Basic Qualifications", "What You Bring", "Qualifications", "Required Experience", "Required Skills". Quote the section verbatim in `requirements_section.text`. If no clear section exists, set `found: false` and leave `text` empty.

2. Decide the verdict honestly — do not inflate:
   - "qualified": clearly meets all hard requirements (degree level, years of experience, must-have skills)
   - "stretch": meets most but is missing 1-2 specific items, or is close on years of experience
   - "not_qualified": missing degree level, fundamental skill, or substantially less experience than required

3. For EACH requirement you identified in step 1, output an entry in `requirements` containing:
   - `requirement`: a short paraphrase of the requirement (e.g., "5+ years Python in production", "PhD in CS or related field")
   - `met`: true if the resume clearly demonstrates it, false otherwise
   - `confidence`: "high" if the resume explicitly supports your judgment, "medium" if you're inferring (e.g., adjacent experience), "low" if it's genuinely ambiguous
   - `evidence`: a specific quote or reference from the resume (e.g., "Resume bullet: 'Developed reproducible Python pipelines' at Colgate Jun 2023-Present, ~3 yrs"). For unmet requirements, write something concrete like "not mentioned in resume" or "resume shows 3 yrs of X vs 5+ required". This is the most important field — the user audits decisions through it.

4. Score the qualification match 0-100 consistent with the verdict: 90+ qualified, 60-89 stretch, <60 not qualified.

5. Assess ATS (Applicant Tracking System) compatibility — distinct from qualification:
   - `keyword_matches`: important keywords from the posting that DO appear in the resume (focus on hard skills, tools, certifications, exact technologies — not generic words like "team" or "develop")
   - `keyword_gaps`: important keywords from the posting that are MISSING from the resume (these are what the candidate should add for keyword density, when they legitimately have the experience)
   - `format_warnings`: resume format issues you can detect that might trip up an ATS parser (tables, complex layouts, unusual section headings) — empty list if none
   - `ats_score`: 0-100 estimate of keyword density alignment. ATS score and qualification score can diverge — a candidate may be highly qualified but lack the exact vocabulary, or keyword-match well without truly meeting requirements.

Be terse. Ground claims in what's actually in the resume."""


def analyze_job(client: anthropic.Anthropic, resume: str, job: dict) -> tuple[JobAnalysis | None, str]:
    """Returns (analysis, posting_text). posting_text is saved for tailor.py."""
    job_text = html_to_text(job["content_html"])
    if len(job_text) < 100:
        return None, job_text

    user_message = (
        f"## Job: {job['title']} at {job['company']}\n"
        f"Location: {job['location']}\n\n"
        f"## Posting:\n{job_text}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=[
                {"type": "text", "text": SYSTEM_INSTRUCTIONS},
                {
                    "type": "text",
                    "text": f"## Candidate Resume\n\n{resume}",
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
            },
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        print(f"    ! Claude API error: {exc}", file=sys.stderr)
        return None, job_text

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        print("    ! Empty response from Claude", file=sys.stderr)
        return None, job_text

    try:
        return JobAnalysis.model_validate_json(text), job_text
    except (json.JSONDecodeError, ValidationError) as exc:
        print(f"    ! Failed to parse analysis: {exc}", file=sys.stderr)
        return None, job_text


# ──────────────────────────────── Main ──────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape, filter, and pre-rank — but don't deep-analyze. "
             "Useful for testing config changes without spending on Sonnet calls.",
    )
    ap.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Override config.yaml's max_jobs (how many to deep-analyze).",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable not set.")
    if not RESUME_PATH.exists():
        sys.exit(f"Error: {RESUME_PATH} not found. Copy resume.example.md and edit.")
    if not CONFIG_PATH.exists():
        sys.exit(f"Error: {CONFIG_PATH} not found.")

    config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    if args.max_jobs is not None:
        config["max_jobs"] = args.max_jobs
    resume = RESUME_PATH.read_text().strip()
    if not resume:
        sys.exit(f"Error: {RESUME_PATH} is empty.")

    has_greenhouse = bool((config.get("greenhouse") or {}).get("companies")) or BOOTSTRAP_PATH.exists()
    has_muse = bool(config.get("muse"))
    if not (has_greenhouse or has_muse):
        sys.exit(
            "Error: config.yaml has no sources enabled "
            "(need either greenhouse.companies or a muse: block)."
        )

    client = anthropic.Anthropic()
    jobs = scrape_jobs(config, client, resume)
    print(f"\nSelected {len(jobs)} jobs for deep analysis.\n", file=sys.stderr)

    if not jobs:
        sys.exit(
            "No jobs matched. Try broadening keywords, raising posted_within_hours, "
            "lowering prerank.threshold, or adding sources in config.yaml."
        )

    if args.dry_run:
        print("Dry run — skipping deep analysis. Selected jobs:\n", file=sys.stderr)
        for j in jobs:
            prerank = j.get("prerank_score")
            ps = f" (prerank {prerank}/10)" if prerank is not None else ""
            print(f"  [{j.get('source','?')}]{ps} {j['title']} @ {j['company']}\n      {j.get('url','')}", file=sys.stderr)
        sys.exit(0)

    results: list[dict] = []

    for i, job in enumerate(jobs, 1):
        prerank = job.get("prerank_score")
        prerank_str = f" (prerank {prerank}/10)" if prerank is not None else ""
        print(
            f"[{i}/{len(jobs)}] [{job.get('source', '?')}]{prerank_str} "
            f"{job['title']} @ {job['company']}",
            file=sys.stderr,
        )
        analysis, posting_text = analyze_job(client, resume, job)
        if analysis is None:
            continue
        posted_at = job.get("posted_at")
        reqs = analysis.qualification_match.requirements
        result = {
            "score": analysis.qualification_match.score,
            "verdict": analysis.qualification_match.verdict,
            "ats_score": analysis.ats_assessment.ats_score,
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "source": job.get("source", ""),
            "url": job["url"],
            "posted_at": posted_at.isoformat() if posted_at else "",
            "prerank_score": prerank,
            # Flat lists derived from structured requirements (for CSV).
            "matched": [r.requirement for r in reqs if r.met],
            "missing": [r.requirement for r in reqs if not r.met],
            # Full per-requirement evidence (in JSON only — see audit.py).
            "requirements_evidence": [r.model_dump() for r in reqs],
            "rationale": analysis.qualification_match.rationale,
            "ats_keyword_matches": analysis.ats_assessment.keyword_matches,
            "ats_keyword_gaps": analysis.ats_assessment.keyword_gaps,
            "ats_format_warnings": analysis.ats_assessment.format_warnings,
            "requirements_found": analysis.requirements_section.found,
            "requirements_heading": analysis.requirements_section.section_heading,
            "requirements_text": analysis.requirements_section.text,
            # Stored for tailor.py and audit.py — full extracted posting text.
            "posting_text": posting_text,
        }
        results.append(result)
        print(
            f"    -> {result['verdict']} ({result['score']}/100), "
            f"ATS {result['ats_score']}/100",
            file=sys.stderr,
        )

    results.sort(key=lambda r: r["score"], reverse=True)

    RESULTS_JSON.write_text(json.dumps(results, indent=2))

    with RESULTS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "score", "verdict", "ats_score", "title", "company", "location",
            "source", "url", "posted_at", "prerank_score",
            "missing", "matched", "ats_keyword_gaps", "ats_keyword_matches",
            "rationale", "ats_format_warnings",
            "requirements_heading", "requirements_text",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "score": r["score"],
                "verdict": r["verdict"],
                "ats_score": r["ats_score"],
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "source": r["source"],
                "url": r["url"],
                "posted_at": r["posted_at"],
                "prerank_score": r["prerank_score"] if r["prerank_score"] is not None else "",
                "missing": "; ".join(r["missing"]),
                "matched": "; ".join(r["matched"]),
                "ats_keyword_gaps": "; ".join(r["ats_keyword_gaps"]),
                "ats_keyword_matches": "; ".join(r["ats_keyword_matches"]),
                "rationale": r["rationale"],
                "ats_format_warnings": "; ".join(r["ats_format_warnings"]),
                "requirements_heading": r["requirements_heading"] or "",
                "requirements_text": (r["requirements_text"] or "")[:800],
            })

    print(
        f"\nDone. Wrote {len(results)} results to {RESULTS_CSV.name} "
        f"and {RESULTS_JSON.name}.\n"
        f"Next: open {RESULTS_CSV.name} (URL column links to apply pages), "
        f"then run `python tailor.py --top 3` to generate tailored resumes.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
