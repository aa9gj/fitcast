# Architecture

## Pipeline overview

```
┌─────────────┐
│ pipeline.py │ ── 1. Scrape ──▶  Greenhouse + Lever + Ashby + Muse
└─────────────┘                    (parallel HTTP, public APIs)
       │
       │ 2. Filter ──▶ keywords / posted_within_hours / applied.json / seen.json
       │
       │ 3. Pre-rank ──▶ Haiku 4.5 scores 0–10 per candidate (cheap)
       │
       │ 4. Deep extract ──▶ Sonnet 4.6 (adaptive thinking)
       │      • Requirements section (verbatim)
       │      • Per-requirement evidence: met / confidence / evidence quote
       │      • Quantitative breakdown: years, degree
       │      • ATS keyword extraction (the LLM half of hybrid ATS)
       │
       │ 5. Score (Python, deterministic) ──▶
       │      • qualification: from met_ratio + degree/years penalties
       │      • ATS: hybrid of O*NET ontology + Claude's keywords
       │      • domain fit: sentence-transformers cosine similarity
       │
       │ 6. Write ──▶ results.csv (sorted by score) + results.json (full data)
       ▼
   results.csv
   results.json  ──▶  python audit.py <url>     (verbose breakdown for one job)
                  ──▶  python tailor.py --top 3  (per-job tailored resume)
                  ──▶  python compare_resumes.py (A/B test resume variants)
```

## The role of each component

| Component | Job | Where the cost is |
|---|---|---|
| **Scrapers** (`fetch_greenhouse_jobs`, `fetch_lever_jobs`, `fetch_ashby_jobs`, `fetch_muse_jobs`) | Pull job listings from public APIs in parallel | Free (public endpoints) |
| **Pre-rank** (Haiku 4.5) | Cheap relevance triage on hundreds of candidates | ~$0.0007 per job (~$0.07 for 100) |
| **Deep extract** (Sonnet 4.6 + adaptive thinking + structured output) | Find requirements, judge each one with evidence, identify quantitative inputs (years/degree), extract ATS keywords | ~$0.025–$0.045 per job |
| **Score derivation** (pure Python) | Apply the transparent formulas to Claude's extraction | Free (local) |
| **Skill extractor** (`skill_extractor.py`) | n-gram match against O*NET ontology | Free (local) |
| **Embedding model** (sentence-transformers) | Cosine similarity for domain fit | Free (local, ~500MB install) |
| **Tailor** (Sonnet 4.6) | Rewrite resume per-job, staying truthful | ~$0.05 per resume |

## Choice of model

Default is **`claude-sonnet-4-6`** for deep analysis and tailoring. The pipeline's job is structured extraction (find the requirements section) + careful rewriting — both well within Sonnet's wheelhouse. Opus 4.7 adds nuance on borderline cases but costs ~2.5× more per token, and rarely changes the top-of-list ordering.

To switch, change `MODEL` at the top of `pipeline.py` and `tailor.py` to `claude-opus-4-7`.

The pre-rank pass uses **`claude-haiku-4-5`** (cheapest, fastest model). The pre-rank prompt is intentionally simple — just a 0–10 relevance score — so Haiku is appropriate.

The embedding model is **`sentence-transformers/all-MiniLM-L6-v2`** — small (~80MB), fast on CPU (~10ms per encode), good enough for resume↔posting topical similarity.

## Design decisions worth knowing

### Why derive scores in Python instead of letting Claude pick numbers?

Earlier versions had Claude return a `score: 42` directly. Reproducibility was poor — same posting could get 42 vs 47 across runs. By moving the math out of the LLM, the score becomes auditable and stable. Claude's job becomes per-requirement judgment + evidence extraction; arithmetic is Python's job.

### Why hybrid ATS instead of pure ontology or pure LLM?

- Pure ontology = fully reproducible but bounded by ~8,800 known skills, no context awareness, no synonyms.
- Pure LLM = broad coverage but mood-dependent.
- Hybrid (union of both) = deterministic baseline + broader coverage.

See [docs/scoring.md](scoring.md) for the formula.

### Why save full posting text in results.json?

So `tailor.py`, `audit.py`, and `compare_resumes.py` can re-analyze any job without re-fetching from the source. Greenhouse occasionally takes postings down, and re-fetching always costs an HTTP call.

### Why both prerank (cheap) and deep-analyze (expensive)?

Token economics. Deep-analyzing 100 candidates would cost ~$3/run. Pre-ranking 100 with Haiku for ~$0.07 then deep-analyzing only the top 10 → ~$0.30/run total. 10× cheaper, same final result quality.

### Why not LinkedIn / Indeed?

They aggressively block scrapers and require auth. Greenhouse / Lever / Ashby / Muse all expose public JSON endpoints with no auth — clean data, no TOS issues.

### Why not auto-apply?

Auto-apply violates essentially every ATS's terms of service and reads as spam to recruiters. The pipeline surfaces ranked opportunities; the application step is yours.

## Source files

| File | Purpose |
|---|---|
| `pipeline.py` | Main scrape → score → rank loop. The orchestrator. |
| `audit.py` | Re-analyze one job in verbose mode (uses pipeline.py's functions) |
| `tailor.py` | Per-job tailored LaTeX resume + LaTeX cover letter (uses Claude web_search to anchor the cover letter in recent company news) |
| `compare_resumes.py` | A/B test multiple resume versions against the same jobs |
| `track.py` | applied.json management |
| `extract_keywords.py` | Suggest config.yaml keywords from resume |
| `bootstrap_companies.py` | Add Greenhouse company slugs from SimplifyJobs (extends config.yaml's list) |
| `bootstrap_ontologies.py` | Download/refresh the O*NET skill catalog (`data/skills.json`) |
| `check_resume_format.py` | Test PDF/DOCX extraction against ATS parsers |
| `skill_extractor.py` | Reusable: load skills.json + n-gram match |
| `data/skills.json` | Pre-built O*NET + supplement skill catalog (committed) |
| `data/skills_supplement.txt` | Hand-curated modern tech terms (editable) |
| `tests/` | pytest suite for pure functions (105 tests) |
| `smoke_test.sh` | Bash script: verify every component end-to-end (`./smoke_test.sh [--paid]`) |
| `fitcast.ipynb` | Colab harness — clones the repo and shells out to `python pipeline.py`/`tailor.py` |
