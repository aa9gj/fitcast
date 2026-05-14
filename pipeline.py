#!/usr/bin/env python3
"""Job hunting pipeline: scrape Greenhouse + The Muse, locate the requirements
section in each posting, derive principled qualification + ATS + domain-fit
scores, and write ranked results to CSV + JSON.

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

from skill_extractor import get_extractor

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
RESUME_PATH = ROOT / "resume.md"
RESULTS_CSV = ROOT / "results.csv"
RESULTS_JSON = ROOT / "results.json"
APPLIED_PATH = ROOT / "applied.json"
SEEN_PATH = ROOT / "seen.json"
BOOTSTRAP_PATH = ROOT / "companies.bootstrap.yaml"

# Sonnet 4.6 is the default for deep analysis. Sonnet does requirements +
# evidence extraction; the qualification + ATS scores are then DERIVED
# in Python from that extraction, not produced by Claude as a vibe number.
MODEL = "claude-sonnet-4-6"

# Haiku 4.5 for the cheap pre-rank pass.
PRERANK_MODEL = "claude-haiku-4-5"

# Embedding model for domain-fit similarity. Optional — the pipeline runs
# fine without sentence-transformers installed (domain_fit_score will be
# None in that case). Install with `pip install sentence-transformers`.
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"
LEVER_BASE = "https://api.lever.co/v0/postings"
ASHBY_BASE = "https://api.ashbyhq.com/posting-api/job-board"
MUSE_BASE = "https://www.themuse.com/api/public/jobs"

# Tunables. Module-level so they're discoverable in one place rather than
# scattered through the fetchers. The user-facing ones (salary bounds,
# min_job_text_chars) are also overridable via config.yaml.
HTTP_TIMEOUT_S = 20
MUSE_MAX_PAGES_DEFAULT = 2
ASHBY_MAX_PAGES_DEFAULT = 1  # Ashby's posting API returns all jobs in one shot.
PRERANK_MAX_WORKERS = 10
SCRAPE_MAX_WORKERS = 10
PRERANK_MAX_TOKENS = 10
PRERANK_FALLBACK_SCORE = 5
ANALYSIS_MAX_TOKENS = 4000
MIN_JOB_TEXT_CHARS = 100
SALARY_FLOOR_USD = 30_000
SALARY_CEILING_USD = 2_000_000
WATCH_MIN_INTERVAL_S = 60


# ─────────────────────────── Error tracking ─────────────────────────────────
# Per-run error counter so the final summary can show what failed without
# the user having to scroll through stderr. Categories are coarse on purpose
# — the goal is "you had 3 fetch failures and 1 parse error," not a full
# log replacement.


class _RunErrors:
    """Per-run counter of errors by category, with helpers to print a summary."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def add(self, category: str) -> None:
        self.counts[category] = self.counts.get(category, 0) + 1

    def reset(self) -> None:
        self.counts = {}

    def summary(self) -> str | None:
        if not self.counts:
            return None
        rows = ", ".join(f"{cat}: {n}" for cat, n in sorted(self.counts.items()))
        total = sum(self.counts.values())
        return f"{total} non-fatal errors during this run ({rows})"


_run_errors = _RunErrors()


# ────────────────────────── Webhook notifications ────────────────────────────
# Optional end-of-run POST when new top matches appear. Designed for cron
# scheduling — you don't have to open results.csv to find out something good
# came up. Compatible with Slack/Discord incoming webhooks and any generic
# JSON endpoint.

WEBHOOK_TIMEOUT_S = 5  # short — failure should never block a successful run


def _build_webhook_payload(new_top_jobs: list[dict], total_results: int) -> dict:
    """Shape sent to the user's webhook. Kept small and stable."""
    return {
        "run_completed_at": datetime.now(timezone.utc).isoformat(),
        "new_top_jobs": [
            {
                "score": r["score"],
                "title": r["title"],
                "company": r["company"],
                "url": r["url"],
                "verdict": r["verdict"],
            }
            for r in new_top_jobs
        ],
        "total_results": total_results,
        "total_new_top_jobs": len(new_top_jobs),
    }


def post_webhook_notification(
    webhook_url: str,
    results: list[dict],
    pre_run_seen_urls: set[str],
    *,
    min_score: int = 70,
    max_jobs: int = 5,
) -> None:
    """POST a summary of new top matches. Failures log but don't crash."""
    if not webhook_url:
        return
    new_top = [
        r for r in results
        if r["url"] not in pre_run_seen_urls and r["score"] >= min_score
    ][:max_jobs]
    if not new_top:
        return
    payload = _build_webhook_payload(new_top, len(results))
    # Slack/Discord webhooks accept "text" as the human-readable summary;
    # generic endpoints get the structured payload. Including both is harmless.
    payload["text"] = (
        f"fitcast: {len(new_top)} new job"
        f"{'s' if len(new_top) != 1 else ''} scored ≥ {min_score}/100. "
        f"Top: {new_top[0]['title']} @ {new_top[0]['company']} "
        f"({new_top[0]['score']}/100)."
    )
    try:
        requests.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT_S)
        print(f"Posted webhook notification ({len(new_top)} new top jobs).", file=sys.stderr)
    except requests.RequestException as exc:
        print(f"  ! webhook POST failed: {exc}", file=sys.stderr)
        _run_errors.add("webhook")


# ────────────────────── Config schema (config.yaml) ─────────────────────────
# Pydantic models with extra="forbid" so a typo in a top-level key (e.g.
# `greenhose:` instead of `greenhouse:`) fails fast with a clear message
# instead of silently disabling that source.


class _StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class GreenhouseConfig(_StrictModel):
    companies: list[str] = Field(default_factory=list)


class LeverConfig(_StrictModel):
    companies: list[str] = Field(default_factory=list)


class AshbyConfig(_StrictModel):
    companies: list[str] = Field(default_factory=list)


class MuseConfig(_StrictModel):
    categories: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_pages: int = MUSE_MAX_PAGES_DEFAULT


class LocationFilterConfig(_StrictModel):
    cities: list[str] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class SalaryFilterConfig(_StrictModel):
    min: int | None = None
    max: int | None = None


class PrerankConfig(_StrictModel):
    enabled: bool = False
    threshold: int = 5
    max_candidates: int = 100


class NotifyConfig(_StrictModel):
    """Optional webhook called at end-of-run when new top matches appear."""
    webhook_url: str | None = None
    min_score: int = 70
    max_jobs: int = 5


class PipelineConfig(_StrictModel):
    greenhouse: GreenhouseConfig | None = None
    lever: LeverConfig | None = None
    ashby: AshbyConfig | None = None
    muse: MuseConfig | None = None
    keywords: list[str] = Field(default_factory=list)
    max_jobs: int = 10
    posted_within_hours: int | str | None = None
    location_filter: LocationFilterConfig | None = None
    salary_filter: SalaryFilterConfig | None = None
    prerank: PrerankConfig | None = None
    notify: NotifyConfig | None = None


def validate_config(raw: dict) -> dict:
    """Validate config.yaml structure. Exits on error; returns raw on success.

    We return the original dict (not the pydantic model) so the rest of the
    code can keep its `config.get("greenhouse", {}).get("companies", [])` style
    without churn. The model is only used here for validation.
    """
    try:
        PipelineConfig.model_validate(raw)
    except ValidationError as exc:
        lines = ["Error: config.yaml has problems:"]
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            lines.append(f"  - {loc}: {err['msg']}")
        lines.append(
            "\nTip: 'extra inputs not permitted' usually means a typo in a "
            "top-level key (e.g. 'greenhose:' instead of 'greenhouse:'). "
            "Check spelling against config.example.yaml."
        )
        sys.exit("\n".join(lines))
    return raw


# ─────────────── Output schema (extracted from Claude) ──────────────────────

class RequirementEvidence(BaseModel):
    requirement: str
    met: bool
    confidence: Literal["high", "medium", "low"]
    evidence: str


class RequirementBreakdown(BaseModel):
    """Quantitative inputs used to derive the qualification score."""
    years_required: int | None = Field(
        description="Years of experience required by the posting, or null if not specified."
    )
    years_resume_estimated: int | None = Field(
        description="Years of relevant industry experience estimated from the resume."
    )
    degree_required: str | None = Field(
        description="Highest degree required by the posting (e.g., 'PhD', 'MS', 'BS'), or null if not specified."
    )
    degree_resume: str | None = Field(
        description="Highest relevant degree on the resume."
    )
    degree_match: Literal["meets_or_exceeds", "below", "unspecified"] = Field(
        description="meets_or_exceeds: resume degree >= required (or no degree required). below: resume below required. unspecified: posting did not specify but resume has a degree."
    )


class RequirementsSection(BaseModel):
    found: bool
    section_heading: str | None = None
    text: str = ""


class QualificationMatch(BaseModel):
    """Pure extraction — no score or verdict here. Both are derived in Python
    from `requirements` + `breakdown` so the math is auditable."""
    requirements: list[RequirementEvidence]
    breakdown: RequirementBreakdown
    rationale: str


class ATSAssessment(BaseModel):
    """Claude-extracted keyword data. Used alongside the O*NET ontology for a
    hybrid ATS score — Claude catches keywords the ontology misses (new tech,
    niche terms, soft skills); the ontology provides a deterministic floor."""
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
                "requirements": {
                    "type": "array",
                    "description": "One entry per requirement found in the posting's requirements section.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "requirement": {"type": "string"},
                            "met": {"type": "boolean"},
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                            "evidence": {"type": "string"},
                        },
                        "required": ["requirement", "met", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                },
                "breakdown": {
                    "type": "object",
                    "properties": {
                        "years_required": {"type": ["integer", "null"]},
                        "years_resume_estimated": {"type": ["integer", "null"]},
                        "degree_required": {"type": ["string", "null"]},
                        "degree_resume": {"type": ["string", "null"]},
                        "degree_match": {
                            "type": "string",
                            "enum": ["meets_or_exceeds", "below", "unspecified"],
                        },
                    },
                    "required": [
                        "years_required",
                        "years_resume_estimated",
                        "degree_required",
                        "degree_resume",
                        "degree_match",
                    ],
                    "additionalProperties": False,
                },
                "rationale": {"type": "string"},
            },
            "required": ["requirements", "breakdown", "rationale"],
            "additionalProperties": False,
        },
        "ats_assessment": {
            "type": "object",
            "properties": {
                "keyword_matches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Important keywords from the posting that DO appear in the resume.",
                },
                "keyword_gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Important keywords from the posting that are MISSING from the resume.",
                },
                "format_warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Resume format issues detectable from text alone (rare with markdown). Use `check_resume_format.py` for the real PDF check.",
                },
            },
            "required": ["keyword_matches", "keyword_gaps", "format_warnings"],
            "additionalProperties": False,
        },
    },
    "required": ["requirements_section", "qualification_match", "ats_assessment"],
    "additionalProperties": False,
}


# ─────────────────────── Score derivation (deterministic) ───────────────────

def derive_qualification_score(qm: QualificationMatch) -> tuple[int, str, dict]:
    """Compute qualification score + verdict from the extracted evidence.

    Formula:
        base = (requirements_met / requirements_total) * 100
        adjustments:
          - degree below required:                   -30
          - years short by N (capped at 6):          -5 per year (max -30)
        score = clamp(base + adjustments, 0, 100)
        verdict: 90+ qualified, 60-89 stretch, <60 not_qualified

    Returns (score, verdict, components_dict).
    """
    reqs = qm.requirements
    total = len(reqs)
    met = sum(1 for r in reqs if r.met)

    if total == 0:
        # No requirements identified — fall back to neutral.
        return 50, "stretch", {
            "requirements_met": 0,
            "requirements_total": 0,
            "met_ratio": None,
            "base_score": 50,
            "degree_penalty": 0,
            "years_penalty": 0,
            "adjustments_total": 0,
        }

    met_ratio = met / total
    base = met_ratio * 100

    degree_penalty = -30 if qm.breakdown.degree_match == "below" else 0

    years_penalty = 0
    if (qm.breakdown.years_required is not None
            and qm.breakdown.years_resume_estimated is not None):
        gap = qm.breakdown.years_required - qm.breakdown.years_resume_estimated
        if gap > 0:
            years_penalty = -min(30, gap * 5)

    adjustments = degree_penalty + years_penalty
    score = max(0, min(100, round(base + adjustments)))
    verdict = "qualified" if score >= 80 else "stretch" if score >= 50 else "not_qualified"

    return score, verdict, {
        "requirements_met": met,
        "requirements_total": total,
        "met_ratio": round(met_ratio, 2),
        "base_score": round(base),
        "degree_penalty": degree_penalty,
        "years_penalty": years_penalty,
        "adjustments_total": adjustments,
    }


def derive_ats_score(
    resume_skills: set[str],
    posting_text: str,
    ats: ATSAssessment,
) -> tuple[int, dict, list[str], list[str]]:
    """Hybrid ATS score: union of O*NET ontology matching + Claude's LLM
    keyword extraction. Score = |union_matched| / |union_total| × 100.

    Why hybrid:
      - Ontology gives a deterministic floor (same posting → same skills,
        every run) but is limited to ~8,800 known skills.
      - LLM extraction catches things the ontology misses (brand-new tech,
        niche tools, soft skills, context-aware mentions) but varies per run.
      - Union of both = best of both: deterministic baseline + broad coverage.

    Returns (score, components_dict, matched_list, missing_list).
    """
    extractor = get_extractor()
    posting_skills = extractor.extract(posting_text)
    onet_matched = resume_skills & posting_skills
    onet_missing = posting_skills - resume_skills

    llm_matched = set(ats.keyword_matches)
    llm_missing = set(ats.keyword_gaps)

    # Union with normalization to dedupe across sources (e.g., "Python" appears
    # in both O*NET and Claude's extraction — should count once).
    def norm(s: str) -> str:
        return s.strip().lower()

    matched_lookup: dict[str, str] = {}
    for s in onet_matched:
        matched_lookup[norm(s)] = s
    for s in llm_matched:
        if norm(s) not in matched_lookup:
            matched_lookup[norm(s)] = s

    missing_lookup: dict[str, str] = {}
    for s in onet_missing:
        missing_lookup[norm(s)] = s
    for s in llm_missing:
        n = norm(s)
        if n not in missing_lookup and n not in matched_lookup:
            missing_lookup[n] = s

    matched = sorted(matched_lookup.values(), key=str.lower)
    missing = sorted(missing_lookup.values(), key=str.lower)
    total = len(matched) + len(missing)

    if total == 0:
        return 50, {
            "skills_matched": 0,
            "skills_total": 0,
            "match_ratio": None,
            "methodology": "hybrid: O*NET ontology + Claude keyword extraction",
            "onet_matched": 0,
            "llm_matched": 0,
            "format_warnings_count": len(ats.format_warnings),
        }, matched, missing

    match_ratio = len(matched) / total
    raw = match_ratio * 100
    # Small penalty for format warnings (more meaningful when running
    # check_resume_format.py on a real PDF).
    warning_penalty = -5 * len(ats.format_warnings)
    score = max(0, min(100, round(raw + warning_penalty)))

    return score, {
        "skills_matched": len(matched),
        "skills_total": total,
        "match_ratio": round(match_ratio, 2),
        "methodology": "hybrid: O*NET ontology + Claude keyword extraction (union)",
        "onet_matched": len(onet_matched),
        "onet_missing": len(onet_missing),
        "llm_matched": len(llm_matched),
        "llm_missing": len(llm_missing),
        "format_warnings_count": len(ats.format_warnings),
        "format_warnings_penalty": warning_penalty,
    }, matched, missing


# ───────────── Domain-fit via embedding similarity (optional dep) ───────────

_st_model = None
_st_util = None


def get_embedding_model():
    """Lazy-load the sentence-transformers model. Returns None if not installed."""
    global _st_model, _st_util
    if _st_model is None:
        try:
            from sentence_transformers import SentenceTransformer, util as st_util
        except ImportError:
            return None
        print(f"Loading embedding model {EMBEDDING_MODEL_NAME} (first run downloads ~80MB)...",
              file=sys.stderr)
        _st_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        _st_util = st_util
    return _st_model


def compute_domain_fit(resume_embedding, posting_text: str) -> int | None:
    """Cosine similarity between resume and posting embeddings, scaled 0-100.
    Returns None if sentence-transformers isn't installed."""
    model = get_embedding_model()
    if model is None or resume_embedding is None:
        return None
    posting_emb = model.encode(posting_text, convert_to_numpy=True)
    sim = _st_util.cos_sim(resume_embedding, posting_emb).item()
    # Cosine sim for text is usually 0-1; clamp just in case.
    return max(0, min(100, round(sim * 100)))


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

_TIME_WINDOW_UNIT_RE = re.compile(r"(\d+)\s*([hdw])", re.IGNORECASE)


def parse_time_window(value) -> int | None:
    """Parse a config value to hours.

    Accepts:
      - integer or numeric string → hours (legacy behavior)
      - "24h" / "48h" → hours
      - "1d" / "7d" → days × 24
      - "2w" / "1w" → weeks × 168
      - "1d12h", "2w3d" → sum of components

    Returns None for None / "" / unparseable input.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 0 else None
    s = str(value).strip().lower()
    if not s:
        return None
    # Plain integer string
    try:
        n = int(s)
        return n if n > 0 else None
    except ValueError:
        pass
    # Composite "1d12h" / "2w" / "48h"
    total_hours = 0
    matched_any = False
    for m in _TIME_WINDOW_UNIT_RE.finditer(s):
        matched_any = True
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "h":
            total_hours += n
        elif unit == "d":
            total_hours += n * 24
        elif unit == "w":
            total_hours += n * 24 * 7
    return total_hours if matched_any and total_hours > 0 else None


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


# ─────────────────────────── Shared HTTP helper ─────────────────────────────

def _safe_get_json(
    url: str,
    *,
    label: str,
    params: dict | list | None = None,
    timeout: int = HTTP_TIMEOUT_S,
    silent_404: bool = True,
):
    """GET a JSON endpoint with consistent error handling.

    Returns the parsed JSON on success, or None on any failure (network error,
    HTTP error, malformed body). 404s are silent by default — fetchers
    over-include slugs and rely on this to skip stale ones cheaply.
    """
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if not (silent_404 and status == 404):
            print(f"  ! {label}: {exc}", file=sys.stderr)
            _run_errors.add(f"fetch:{label.split('/')[0]}")
        return None
    try:
        return resp.json()
    except ValueError as exc:
        # Upstream returned 200 with HTML, empty body, or other non-JSON
        # content. Before this guard, any of these would crash the entire run.
        print(f"  ! {label}: invalid JSON response ({exc})", file=sys.stderr)
        _run_errors.add(f"fetch:{label.split('/')[0]}")
        return None


# ─────────────────────────── Source: Greenhouse ─────────────────────────────

def fetch_greenhouse_jobs(slug: str) -> list[dict]:
    data = _safe_get_json(
        f"{GREENHOUSE_BASE}/{slug}/jobs",
        params={"content": "true"},
        label=f"greenhouse/{slug}",
    )
    if not data:
        return []
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


# ─────────────────────────── Source: Lever ──────────────────────────────────

def fetch_lever_jobs(slug: str) -> list[dict]:
    """Fetch all jobs from one Lever public board (returns JSON array)."""
    data = _safe_get_json(
        f"{LEVER_BASE}/{slug}",
        params={"mode": "json"},
        label=f"lever/{slug}",
    )
    if not isinstance(data, list):
        return []
    jobs = []
    for j in data:
        # Lever createdAt is a unix-millisecond timestamp.
        created_ms = j.get("createdAt")
        posted_at = None
        if isinstance(created_ms, (int, float)):
            try:
                posted_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                posted_at = None
        location = (j.get("categories") or {}).get("location", "")
        # Lever has both `description` (HTML) and `descriptionPlain`. Use HTML
        # to be consistent with Greenhouse/Ashby; html_to_text handles both.
        content_html = j.get("description") or j.get("descriptionPlain") or ""
        jobs.append({
            "source": "lever",
            "id": str(j.get("id", "")),
            "title": j.get("text", ""),
            "company": slug,
            "location": location,
            "url": j.get("hostedUrl", ""),
            "content_html": content_html,
            "posted_at": posted_at,
        })
    return jobs


# ─────────────────────────── Source: Ashby ──────────────────────────────────

def fetch_ashby_jobs(slug: str) -> list[dict]:
    """Fetch all jobs from one Ashby public job board."""
    data = _safe_get_json(
        f"{ASHBY_BASE}/{slug}",
        label=f"ashby/{slug}",
    )
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "source": "ashby",
            "id": str(j.get("id", "")),
            "title": j.get("title", ""),
            "company": slug,
            "location": j.get("location", ""),
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "content_html": j.get("descriptionHtml", "") or "",
            "posted_at": _parse_iso(j.get("publishedAt")),
        })
    return jobs


# ─────────────────────────── Source: The Muse ───────────────────────────────

def fetch_muse_jobs(
    categories: list[str],
    levels: list[str],
    locations: list[str],
    max_pages: int = MUSE_MAX_PAGES_DEFAULT,
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

        data = _safe_get_json(
            MUSE_BASE,
            params=params,
            label="themuse",
            silent_404=False,
        )
        if not data:
            break

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


def load_seen_urls() -> dict[str, dict]:
    """Return dict of url -> {first_seen, last_seen, title, company}."""
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def update_seen_urls(seen: dict[str, dict], jobs: list[dict]) -> None:
    """Mark jobs as seen, persist to seen.json. Updates last_seen for repeats."""
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        url = job.get("url") or ""
        if not url:
            continue
        if url in seen:
            seen[url]["last_seen"] = now
        else:
            seen[url] = {
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "first_seen": now,
                "last_seen": now,
            }
    try:
        SEEN_PATH.write_text(json.dumps(seen, indent=2, sort_keys=True))
    except OSError as exc:
        print(f"  ! couldn't update {SEEN_PATH.name}: {exc}", file=sys.stderr)


def load_bootstrap_companies() -> dict[str, list[str]]:
    """Return bootstrapped slugs per source. Empty lists if no bootstrap file.

    Always returns a dict with keys: 'greenhouse', 'lever', 'ashby'. Callers
    can index without guarding for missing keys.
    """
    empty = {"greenhouse": [], "lever": [], "ashby": []}
    if not BOOTSTRAP_PATH.exists():
        return empty
    try:
        data = yaml.safe_load(BOOTSTRAP_PATH.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return empty
    return {
        "greenhouse": data.get("greenhouse_companies") or [],
        "lever": data.get("lever_companies") or [],
        "ashby": data.get("ashby_companies") or [],
    }


def enabled_sources(config: dict) -> list[str]:
    """Return enabled source names from config and generated bootstrap data."""
    bootstrap = load_bootstrap_companies() if BOOTSTRAP_PATH.exists() else {}
    sources: list[str] = []
    if ((config.get("greenhouse") or {}).get("companies")) or bootstrap.get("greenhouse"):
        sources.append("greenhouse")
    if ((config.get("lever") or {}).get("companies")) or bootstrap.get("lever"):
        sources.append("lever")
    if ((config.get("ashby") or {}).get("companies")) or bootstrap.get("ashby"):
        sources.append("ashby")
    if config.get("muse"):
        sources.append("muse")
    return sources


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
            max_tokens=PRERANK_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.APIError, anthropic.APIConnectionError, anthropic.APITimeoutError, ValueError) as exc:
        # Don't crash the whole batch over one job — return the neutral
        # midpoint so it sorts behind clearer signals. Network blips,
        # transient 5xxs, and malformed responses all land here.
        print(f"    ! prerank failed for {job.get('title','?')!r}: {exc}", file=sys.stderr)
        _run_errors.add("prerank")
        return PRERANK_FALLBACK_SCORE
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    m = re.search(r"\d+", text)
    if not m:
        return PRERANK_FALLBACK_SCORE
    return max(0, min(10, int(m.group(0))))


def prerank_jobs(
    client: anthropic.Anthropic,
    resume: str,
    jobs: list[dict],
    threshold: int,
    max_candidates: int,
    max_workers: int = PRERANK_MAX_WORKERS,
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


def matches_location(job: dict, location_filter: dict | None) -> bool:
    """Apply location include / exclude / cities rules.

    - `exclude`: substring match (case-insensitive). Any match drops the job.
    - `cities`: word-boundary match (case-insensitive). Avoids "MA" matching
      "Manila" by requiring \\b boundaries. Any match passes.
    - `include`: substring match (case-insensitive). Any match passes.
    - exclude wins; cities OR include passes; empty filter = pass everything.
    """
    if not location_filter:
        return True
    location = (job.get("location") or "").lower()

    excludes = [s.lower() for s in (location_filter.get("exclude") or [])]
    if location and any(ex in location for ex in excludes):
        return False

    cities = [s.lower() for s in (location_filter.get("cities") or [])]
    includes = [s.lower() for s in (location_filter.get("include") or [])]

    # No inclusion criteria → pass through (only exclude was checked above).
    if not cities and not includes:
        return True

    # Cities: word-boundary match (avoids "MA" in "Manila", "SF" in "Salford").
    for city in cities:
        if re.search(r"\b" + re.escape(city) + r"\b", location):
            return True

    # Includes: substring match (catches "remote", "hybrid", country names).
    for inc in includes:
        if inc in location:
            return True

    return False


# Salary patterns we recognize in posting text. Tries to be conservative —
# better to miss some than to misfire on, say, equity numbers or revenue
# stats mentioned in company descriptions.
_SALARY_FULL_RE = re.compile(
    r'\$\s?(\d{1,3}(?:,\d{3})+)(?!\.\d)',  # e.g., $120,000 or $1,250,000
)
_SALARY_K_RE = re.compile(
    r'\$\s?(\d{2,3}(?:\.\d+)?)\s?[Kk](?![a-zA-Z])',  # e.g., $120k or $120K
)


def extract_salaries(text: str) -> list[int]:
    """Extract plausible USD annual salary figures from text.

    Looks for:
      - $X,XXX[,XXX] patterns (always)
      - $XXk patterns (e.g., $120k → 120000)

    Filters out values outside [$30K, $2M] — anything else is unlikely
    to be an annual base salary (probably hourly rates, totals, etc.).
    """
    out: list[int] = []
    for m in _SALARY_FULL_RE.finditer(text):
        try:
            n = int(m.group(1).replace(",", ""))
        except (ValueError, TypeError):
            continue
        if SALARY_FLOOR_USD <= n <= SALARY_CEILING_USD:
            out.append(n)
    for m in _SALARY_K_RE.finditer(text):
        try:
            n = int(float(m.group(1)) * 1000)
        except (ValueError, TypeError):
            continue
        if SALARY_FLOOR_USD <= n <= SALARY_CEILING_USD:
            out.append(n)
    return out


def matches_salary(job: dict, salary_filter: dict | None) -> bool:
    """Check if the job's salary range overlaps the configured user range.

    Filter config accepts independent `min` and `max` (either or both):
        salary_filter:
          min: 100000   # USD/year — drop if posting's max < this
          max: 250000   # USD/year — drop if posting's min > this

    Semantics: drop the job ONLY if its salary range is ENTIRELY outside the
    user's range. Postings that overlap pass — they might still hit the
    user's target. Postings with no salary mentioned PASS THROUGH (we don't
    penalize non-disclosing companies).
    """
    if not salary_filter:
        return True

    user_min = salary_filter.get("min")
    user_max = salary_filter.get("max")
    if user_min in (None, 0) and user_max in (None, 0):
        return True

    text = (job.get("title", "") + " " + job.get("content_html", ""))
    salaries = extract_salaries(text)
    if not salaries:
        return True  # not mentioned → pass through

    posting_min = min(salaries)
    posting_max = max(salaries)

    # Drop if posting is entirely below user_min.
    if user_min not in (None, 0) and posting_max < int(user_min):
        return False
    # Drop if posting is entirely above user_max.
    if user_max not in (None, 0) and posting_min > int(user_max):
        return False
    return True


def _parallel_fetch(label: str, slugs: list[str], fetch_fn) -> list[dict]:
    """Run fetch_fn(slug) in parallel for all slugs. Returns flattened job list."""
    if not slugs:
        return []
    print(f"Scraping {len(slugs)} {label} boards in parallel...", file=sys.stderr)
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=SCRAPE_MAX_WORKERS) as ex:
        for jobs in ex.map(fetch_fn, slugs):
            out.extend(jobs)
    print(f"  {label}: {len(out)} jobs", file=sys.stderr)
    return out


def _collect_from_sources(config: dict) -> list[dict]:
    """Fetch jobs from every enabled source. Returns flat list."""
    all_jobs: list[dict] = []
    bootstrap = load_bootstrap_companies()

    def _merge_slugs(configured: list[str], bootstrapped: list[str], label: str) -> list[str]:
        """Configured slugs first (user's curation wins ordering), then bootstrap."""
        merged = list(dict.fromkeys(list(configured) + list(bootstrapped)))
        if merged:
            print(
                f"({len(configured)} configured + {len(bootstrapped)} bootstrapped {label})",
                file=sys.stderr,
            )
        return merged

    gh_companies = ((config.get("greenhouse") or {}).get("companies")) or []
    gh_all = _merge_slugs(gh_companies, bootstrap.get("greenhouse", []), "Greenhouse")
    all_jobs.extend(_parallel_fetch("greenhouse", gh_all, fetch_greenhouse_jobs))

    lever_companies = ((config.get("lever") or {}).get("companies")) or []
    lever_all = _merge_slugs(lever_companies, bootstrap.get("lever", []), "Lever")
    all_jobs.extend(_parallel_fetch("lever", lever_all, fetch_lever_jobs))

    ashby_companies = ((config.get("ashby") or {}).get("companies")) or []
    ashby_all = _merge_slugs(ashby_companies, bootstrap.get("ashby", []), "Ashby")
    all_jobs.extend(_parallel_fetch("ashby", ashby_all, fetch_ashby_jobs))

    muse_cfg = config.get("muse")
    if muse_cfg:
        print("Querying The Muse...", file=sys.stderr)
        muse_jobs = fetch_muse_jobs(
            categories=muse_cfg.get("categories") or [],
            levels=muse_cfg.get("levels") or [],
            locations=muse_cfg.get("locations") or [],
            max_pages=int(muse_cfg.get("max_pages", MUSE_MAX_PAGES_DEFAULT)),
        )
        print(f"  themuse: {len(muse_jobs)} jobs", file=sys.stderr)
        all_jobs.extend(muse_jobs)

    return all_jobs


def _apply_filters(jobs: list[dict], config: dict, include_seen: bool) -> list[dict]:
    """Apply keyword + time + location + salary + applied + seen filters."""
    keywords = config.get("keywords") or []
    posted_within_hours = config.get("posted_within_hours")

    filtered = [j for j in jobs if matches_keywords(j, keywords)]

    posted_hours = parse_time_window(posted_within_hours)
    if posted_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=posted_hours)
        before = len(filtered)
        filtered = [j for j in filtered if j.get("posted_at") and j["posted_at"] >= cutoff]
        label = f"{posted_within_hours}" if posted_within_hours else f"{posted_hours}h"
        print(f"  date filter (last {label}): {before} -> {len(filtered)}", file=sys.stderr)

    location_filter = config.get("location_filter")
    if location_filter:
        before = len(filtered)
        filtered = [j for j in filtered if matches_location(j, location_filter)]
        print(f"  location filter: {before} -> {len(filtered)}", file=sys.stderr)

    salary_filter = config.get("salary_filter")
    if salary_filter and (salary_filter.get("min") or salary_filter.get("max")):
        before = len(filtered)
        filtered = [j for j in filtered if matches_salary(j, salary_filter)]
        smin, smax = salary_filter.get("min"), salary_filter.get("max")
        if smin and smax:
            label = f"${int(smin):,}–${int(smax):,}"
        elif smin:
            label = f">= ${int(smin):,}"
        else:
            label = f"<= ${int(smax):,}"
        print(
            f"  salary filter ({label} or unstated): {before} -> {len(filtered)}",
            file=sys.stderr,
        )

    applied_urls = load_applied_urls()
    if applied_urls:
        before = len(filtered)
        filtered = [j for j in filtered if (j.get("url") or "") not in applied_urls]
        print(f"  skipping {before - len(filtered)} already-applied jobs", file=sys.stderr)

    seen_record = load_seen_urls()
    if seen_record and not include_seen:
        seen_url_set = set(seen_record.keys())
        before = len(filtered)
        filtered = [j for j in filtered if (j.get("url") or "") not in seen_url_set]
        print(
            f"  skipping {before - len(filtered)} jobs already seen in a prior run "
            f"(use --include-seen to override)",
            file=sys.stderr,
        )

    return filtered


def _dedupe_by_url(jobs: list[dict]) -> list[dict]:
    """Drop duplicate URLs (same posting can appear in multiple sources)."""
    seen: set[str] = set()
    out: list[dict] = []
    for j in jobs:
        url = j.get("url") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        out.append(j)
    return out


def _select_top_jobs(
    jobs: list[dict],
    config: dict,
    client: anthropic.Anthropic,
    resume: str,
) -> list[dict]:
    """Pick the final pool: prerank with Haiku if enabled, else random sample."""
    max_jobs = int(config.get("max_jobs", 10))
    prerank_cfg = config.get("prerank") or {}
    if prerank_cfg.get("enabled", False) and len(jobs) > max_jobs:
        ranked = prerank_jobs(
            client=client,
            resume=resume,
            jobs=jobs,
            threshold=int(prerank_cfg.get("threshold", 5)),
            max_candidates=int(prerank_cfg.get("max_candidates", 100)),
        )
        return ranked[:max_jobs]

    random.seed(42)
    pool = list(jobs)
    random.shuffle(pool)
    return pool[:max_jobs]


def scrape_jobs(config: dict, client: anthropic.Anthropic, resume: str,
                include_seen: bool = False) -> list[dict]:
    """Scrape → filter → dedupe → select. Composition of testable stages."""
    all_jobs = _collect_from_sources(config)
    fetched = len(all_jobs)
    filtered = _apply_filters(all_jobs, config, include_seen)
    deduped = _dedupe_by_url(filtered)
    print(
        f"Pool: {fetched} fetched -> {len(filtered)} after filters -> "
        f"{len(deduped)} after dedup",
        file=sys.stderr,
    )
    return _select_top_jobs(deduped, config, client, resume)


# ──────────────────────────── Claude analysis ───────────────────────────────

SYSTEM_INSTRUCTIONS = """You are a careful job-application analyst. For each job posting, EXTRACT the following — your job is per-requirement extraction and keyword identification, not picking aggregate scores. The pipeline computes the qualification + ATS scores from your extraction.

1. Find the section that lists minimum requirements / qualifications. Common headings: "Requirements", "Minimum Qualifications", "Basic Qualifications", "What You Bring", "Qualifications", "Required Experience". Quote it verbatim in `requirements_section.text`. If no clear section exists, set `found: false`.

2. For EACH requirement you identified, output an entry in `qualification_match.requirements`:
   - `requirement`: a short paraphrase (e.g., "5+ years Python in production")
   - `met`: true if the resume clearly demonstrates it, false otherwise
   - `confidence`: "high" if the resume explicitly supports your judgment, "medium" if you're inferring (adjacent experience), "low" if it's genuinely ambiguous
   - `evidence`: a specific quote/reference from the resume (e.g., "Resume: 'Developed reproducible Python pipelines' at Colgate Jun 2023-Present, ~3 yrs"). For unmet requirements, write something concrete like "not mentioned in resume" or "resume shows 3 yrs vs 5+ required". This is the most important field — the user audits decisions through it.

3. Fill `qualification_match.breakdown` honestly with the QUANTITATIVE inputs:
   - `years_required`: integer years of experience the posting requires (e.g. 5 for "5+ years"). Null if not stated.
   - `years_resume_estimated`: integer estimate of relevant industry experience from the resume (post-degree, in roles relevant to the job). Round down.
   - `degree_required`: highest degree the posting requires (e.g., "PhD", "MS", "BS"). Null if not stated.
   - `degree_resume`: highest relevant degree on the resume.
   - `degree_match`: "meets_or_exceeds" if resume degree >= required (or if no degree was required), "below" if resume degree is lower, "unspecified" if posting didn't specify.

4. Provide a 2-3 sentence `rationale` summarizing the overall fit honestly.

5. Extract ATS keywords. The pipeline ALSO runs a deterministic O*NET ontology match independently — your job here is to catch the things the ontology might MISS: brand-new tech, niche tools, soft skills, multi-word phrases not in O*NET. Don't bother re-listing common skills (Python, SQL) — they'll be caught by the ontology. Focus on what's distinctive about THIS specific posting.
   - `keyword_matches`: distinctive keywords from the posting that DO appear in the resume (only include things that aren't trivially obvious — focus on non-O*NET terms)
   - `keyword_gaps`: distinctive keywords from the posting that are MISSING from the resume
   - `format_warnings`: any format issues you can detect from the resume markdown (almost always empty for markdown input — empty list is fine)

Be terse. Ground every claim in what's actually in the resume. The Python pipeline derives the scores from this extraction — do NOT pick scores yourself."""


def analyze_job(client: anthropic.Anthropic, resume: str, job: dict) -> tuple[JobAnalysis | None, str]:
    """Returns (analysis, posting_text). posting_text is saved for tailor.py / audit.py."""
    job_text = html_to_text(job["content_html"])
    if len(job_text) < MIN_JOB_TEXT_CHARS:
        _run_errors.add("analyze:short_posting")
        return None, job_text

    user_message = (
        f"## Job: {job['title']} at {job['company']}\n"
        f"Location: {job['location']}\n\n"
        f"## Posting:\n{job_text}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=ANALYSIS_MAX_TOKENS,
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
        _run_errors.add("analyze:api")
        return None, job_text

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        print("    ! Empty response from Claude", file=sys.stderr)
        _run_errors.add("analyze:empty")
        return None, job_text

    try:
        return JobAnalysis.model_validate_json(text), job_text
    except (json.JSONDecodeError, ValidationError) as exc:
        print(f"    ! Failed to parse analysis: {exc}", file=sys.stderr)
        _run_errors.add("analyze:parse")
        return None, job_text


# ──────────────────────────────── Main ──────────────────────────────────────

def main() -> None:
    # Fresh error counter for this run — important for --watch mode so each
    # iteration's summary reflects only that iteration.
    _run_errors.reset()

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
    ap.add_argument(
        "--include-seen",
        action="store_true",
        help="Re-analyze jobs that were seen in prior runs "
             "(by default, jobs in seen.json are skipped to avoid duplicate spend).",
    )
    ap.add_argument(
        "--watch",
        action="store_true",
        help="(Prefer cron — see docs/customizing.md.) Loop forever: run, sleep "
             "--interval, run again. Use Ctrl-C to stop. Each run writes "
             "results.csv (overwriting). Cron gives you logs, email-on-failure, "
             "and survives terminal/laptop sleep — --watch does not.",
    )
    ap.add_argument(
        "--interval",
        default="24h",
        help="When using --watch, time between runs. Examples: 24h, 12h, 6h, 30m, 2h30m.",
    )
    ap.add_argument(
        "--notify-webhook",
        default=None,
        help="Override config.yaml's notify.webhook_url. POSTs a JSON summary of "
             "new top matches (score >= notify.min_score) at end of run. Works "
             "with Slack/Discord incoming webhooks and any generic JSON endpoint.",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable not set.")
    if not RESUME_PATH.exists():
        sys.exit(f"Error: {RESUME_PATH} not found. Copy resume.example.md and edit.")
    if not CONFIG_PATH.exists():
        sys.exit(f"Error: {CONFIG_PATH} not found.")

    config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    validate_config(config)
    if args.max_jobs is not None:
        config["max_jobs"] = args.max_jobs
    resume = RESUME_PATH.read_text().strip()
    if not resume:
        sys.exit(f"Error: {RESUME_PATH} is empty.")

    if not enabled_sources(config):
        sys.exit(
            "Error: config.yaml has no sources enabled "
            "(enable at least one of: greenhouse.companies, lever.companies, "
            "ashby.companies, or a muse: block)."
        )

    # Snapshot which URLs are already in seen.json — used at end of run to
    # decide which "top match" results should trigger the webhook (avoiding
    # repeat pings for jobs you've already inspected once).
    pre_run_seen_urls = set(load_seen_urls().keys())

    client = anthropic.Anthropic()
    jobs = scrape_jobs(config, client, resume, include_seen=args.include_seen)
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
            print(
                f"  [{j.get('source','?')}]{ps} {j['title']} @ {j['company']}\n"
                f"      {j.get('url','')}",
                file=sys.stderr,
            )
        sys.exit(0)

    # Pre-compute resume embedding once if sentence-transformers is available.
    resume_embedding = None
    model = get_embedding_model()
    if model is not None:
        resume_embedding = model.encode(resume, convert_to_numpy=True)
    else:
        print("(sentence-transformers not installed — domain_fit_score will be omitted; "
              "install with `pip install sentence-transformers` to enable)",
              file=sys.stderr)

    # Pre-extract skills from the resume once (used for every job's ATS score).
    extractor = get_extractor()
    resume_skills = extractor.extract(resume)
    print(f"Resume skills (O*NET-matched): {len(resume_skills)} found "
          f"[{extractor.info()}]", file=sys.stderr)

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

        # Derive scores deterministically from the extraction.
        qm = analysis.qualification_match
        ats = analysis.ats_assessment
        score, verdict, score_components = derive_qualification_score(qm)
        ats_score, ats_components, ats_matched, ats_missing = derive_ats_score(
            resume_skills, posting_text, ats
        )
        domain_fit = compute_domain_fit(resume_embedding, posting_text) if resume_embedding is not None else None

        posted_at = job.get("posted_at")

        # Pull any USD salary amounts mentioned in the posting (min + max
        # across all matches). None if the posting doesn't mention salary.
        salaries_found = extract_salaries(
            (job.get("title", "") or "") + " " + (job.get("content_html", "") or "")
        )
        salary_min_extracted = min(salaries_found) if salaries_found else None
        salary_max_extracted = max(salaries_found) if salaries_found else None

        result = {
            "score": score,
            "verdict": verdict,
            "ats_score": ats_score,
            "domain_fit_score": domain_fit,
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "source": job.get("source", ""),
            "url": job["url"],
            "posted_at": posted_at.isoformat() if posted_at else "",
            "salary_min_extracted": salary_min_extracted,
            "salary_max_extracted": salary_max_extracted,
            "prerank_score": prerank,
            "matched": [r.requirement for r in qm.requirements if r.met],
            "missing": [r.requirement for r in qm.requirements if not r.met],
            "rationale": qm.rationale,
            # ATS skills — hybrid of O*NET ontology + Claude's LLM keyword
            # extraction, deduplicated. Components show the source breakdown.
            "ats_skills_matched": ats_matched,
            "ats_skills_missing": ats_missing,
            "ats_format_warnings": ats.format_warnings,
            "requirements_found": analysis.requirements_section.found,
            "requirements_heading": analysis.requirements_section.section_heading,
            "requirements_text": analysis.requirements_section.text,
            # Score derivation transparency.
            "score_components": score_components,
            "ats_components": ats_components,
            "breakdown": qm.breakdown.model_dump(),
            # Full per-requirement evidence (in JSON only — see audit.py).
            "requirements_evidence": [r.model_dump() for r in qm.requirements],
            # Stored for tailor.py and audit.py.
            "posting_text": posting_text,
        }
        results.append(result)
        df_str = f", domain {domain_fit}/100" if domain_fit is not None else ""
        print(
            f"    -> {verdict} ({score}/100), ATS {ats_score}/100{df_str}  "
            f"[{score_components.get('requirements_met', 0)}/{score_components.get('requirements_total', 0)} reqs met, "
            f"{ats_components.get('skills_matched', 0)}/{ats_components.get('skills_total', 0)} skills]",
            file=sys.stderr,
        )

    if not results:
        sys.exit(
            "No jobs could be analyzed successfully. Check the Claude API errors above, "
            "verify that selected postings have enough text, or rerun with --dry-run to "
            "inspect the selected jobs without spending on analysis."
        )

    results.sort(key=lambda r: r["score"], reverse=True)

    update_seen_urls(load_seen_urls(), results)

    RESULTS_JSON.write_text(json.dumps(results, indent=2))

    with RESULTS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "score", "verdict", "ats_score", "domain_fit_score",
            "title", "company", "location", "source", "url", "posted_at",
            "salary_min_extracted", "salary_max_extracted",
            "prerank_score",
            "requirements_met", "requirements_total",
            "missing", "matched",
            "ats_skills_missing", "ats_skills_matched",
            "rationale", "ats_format_warnings",
            "requirements_heading", "requirements_text",
        ])
        writer.writeheader()
        for r in results:
            sc = r["score_components"]
            writer.writerow({
                "score": r["score"],
                "verdict": r["verdict"],
                "ats_score": r["ats_score"],
                "domain_fit_score": r["domain_fit_score"] if r["domain_fit_score"] is not None else "",
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "source": r["source"],
                "url": r["url"],
                "posted_at": r["posted_at"],
                "salary_min_extracted": r["salary_min_extracted"] if r["salary_min_extracted"] is not None else "",
                "salary_max_extracted": r["salary_max_extracted"] if r["salary_max_extracted"] is not None else "",
                "prerank_score": r["prerank_score"] if r["prerank_score"] is not None else "",
                "requirements_met": sc.get("requirements_met", ""),
                "requirements_total": sc.get("requirements_total", ""),
                "missing": "; ".join(r["missing"]),
                "matched": "; ".join(r["matched"]),
                "ats_skills_missing": "; ".join(r["ats_skills_missing"]),
                "ats_skills_matched": "; ".join(r["ats_skills_matched"]),
                "rationale": r["rationale"],
                "ats_format_warnings": "; ".join(r["ats_format_warnings"]),
                "requirements_heading": r["requirements_heading"] or "",
                "requirements_text": (r["requirements_text"] or "")[:800],
            })

    # Webhook is optional: explicit --notify-webhook wins, otherwise fall back
    # to config.yaml's notify block.
    notify_cfg = config.get("notify") or {}
    webhook_url = args.notify_webhook or notify_cfg.get("webhook_url")
    if webhook_url:
        post_webhook_notification(
            webhook_url,
            results,
            pre_run_seen_urls,
            min_score=int(notify_cfg.get("min_score", 70)),
            max_jobs=int(notify_cfg.get("max_jobs", 5)),
        )

    summary = _run_errors.summary()
    if summary:
        print(f"\nNote: {summary}", file=sys.stderr)

    print(
        f"\nDone. Wrote {len(results)} results to {RESULTS_CSV.name} "
        f"and {RESULTS_JSON.name}.\n"
        f"Next: open {RESULTS_CSV.name} (URL column links to apply pages), "
        f"then run `python tailor.py --top 3` to generate tailored resumes, "
        f"or `python audit.py <url>` to inspect any score in detail.",
        file=sys.stderr,
    )


def _parse_interval(s: str) -> int:
    """Parse '24h', '12h30m', '90m', '6h' etc. into seconds."""
    s = s.strip().lower()
    pattern = re.compile(r"(\d+)\s*([hms])")
    matches = pattern.findall(s)
    if not matches:
        raise ValueError(f"Couldn't parse interval {s!r}. Use formats like '24h', '12h30m', '6h'.")
    seconds = 0
    for n, unit in matches:
        n = int(n)
        if unit == "h":
            seconds += n * 3600
        elif unit == "m":
            seconds += n * 60
        elif unit == "s":
            seconds += n
    if seconds < WATCH_MIN_INTERVAL_S:
        raise ValueError(
            f"Interval too short: {seconds}s. Minimum is {WATCH_MIN_INTERVAL_S}s "
            "to avoid hammering APIs."
        )
    return seconds


if __name__ == "__main__":
    import argparse as _ap
    import time as _time

    # Re-parse args at the top level so --watch is visible here too.
    _parser = _ap.ArgumentParser(add_help=False)
    _parser.add_argument("--watch", action="store_true")
    _parser.add_argument("--interval", default="24h")
    _watch_args, _ = _parser.parse_known_args()

    if not _watch_args.watch:
        main()
    else:
        try:
            interval_s = _parse_interval(_watch_args.interval)
        except ValueError as exc:
            sys.exit(f"Error: {exc}")
        print(f"Watch mode: re-running every {_watch_args.interval} ({interval_s}s). Ctrl-C to stop.\n",
              file=sys.stderr)
        run_n = 0
        while True:
            run_n += 1
            print(f"\n{'═' * 60}\nRun #{run_n} at {datetime.now(timezone.utc).isoformat()}\n{'═' * 60}\n",
                  file=sys.stderr)
            try:
                main()
            except SystemExit as exc:
                if exc.code:
                    print(f"  pipeline exited with: {exc}", file=sys.stderr)
            except Exception as exc:
                print(f"  pipeline raised: {type(exc).__name__}: {exc}", file=sys.stderr)
            print(f"\nSleeping {_watch_args.interval} until next run...", file=sys.stderr)
            try:
                _time.sleep(interval_s)
            except KeyboardInterrupt:
                print("\nInterrupted — exiting watch mode.", file=sys.stderr)
                break
