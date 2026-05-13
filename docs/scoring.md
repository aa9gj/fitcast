# How scoring works

Each job gets **four scores**. None of them are LLM-judged numbers (with one exception, called out below). Three are *derived deterministically* from explicit inputs: per-requirement evidence; ontology-based skill overlap; embedding similarity. Claude's role is structured extraction; Python does the arithmetic.

## TL;DR

| Score | Range | Source | What it measures |
|---|---|---|---|
| `prerank_score` | 0–10 | LLM (Haiku) | "Is this even plausibly relevant?" — cheap pre-filter |
| `score` (qualification) | 0–100 | Derived | "Do I meet the actual requirements?" |
| `ats_score` | 0–100 | Derived | "How many of the posting's keywords are in my resume?" |
| `domain_fit_score` | 0–100 | Embedding model | "Is this in my field at all?" — captures topical similarity |

Same posting + same resume → same qualification score. Same ATS score (modulo one LLM-extraction step in the hybrid). Same domain fit. Pre-rank can vary slightly across runs.

---

## 1. Pre-rank score (0–10, Haiku 4.5 — LLM judgment)

A cheap one-shot relevance score generated *before* the expensive deep analysis. Asks: "given the candidate's resume, how plausibly relevant is this job at all?"

- **0–3**: clearly unrelated
- **4–6**: tangentially related — maybe worth considering
- **7–10**: clearly relevant

Only jobs scoring ≥ `prerank.threshold` (default 5) survive to deep analysis. Raise the threshold to 7+ if you're seeing too many irrelevant jobs in your `results.csv`.

This is the only score still pure LLM judgment — it runs on 100+ candidates and needs to be cheap. Investing more here doesn't justify the cost.

## 2. Qualification score (0–100, derived from evidence)

Claude extracts per-requirement evidence and quantitative inputs; the pipeline DERIVES the score using a transparent formula:

```
base_score = (requirements_met / requirements_total) × 100

degree_penalty = -30 if resume degree is below the posting's requirement, else 0
years_penalty  = -5 per year short on experience, capped at -30

score = clamp(base_score + degree_penalty + years_penalty, 0, 100)
verdict = "qualified"     if score >= 80
        else "stretch"    if score >= 50
        else "not_qualified"
```

Every input is in `results.json` under `score_components` and `breakdown`:

| Field | Example |
|---|---|
| `requirements_met` / `requirements_total` | 6 / 9 |
| `met_ratio` | 0.67 |
| `base_score` | 67 |
| `years_required` / `years_resume_estimated` | 5 / 3 |
| `years_penalty` | -10 |
| `degree_required` / `degree_resume` / `degree_match` | "PhD" / "PhD" / "meets_or_exceeds" |
| `degree_penalty` | 0 |
| Final `score` | 57 → `stretch` |

You can hand-check any score: "6/9 = 67 base, minus 10 for years, equals 57 → stretch." Run `python audit.py <url>` to print this breakdown for any job.

## 3. ATS score (0–100, hybrid: O*NET ontology + LLM extraction)

Two independent skill-extraction passes are run on both the posting and the resume; the results are unioned and deduplicated.

### Pass A — O*NET ontology (deterministic)

Both texts are scanned against an explicit ontology — [O*NET](https://www.onetonline.org/) (US Department of Labor's occupational skills database, public domain) plus a curated supplement of modern tech/data/methodology terms it doesn't index (`data/skills_supplement.txt`). The skill catalog (`data/skills.json`) ships with the repo: ~8,800 skills after filtering noise. **Same text → exact same skills, every run.**

### Pass B — Claude keyword extraction (broad coverage)

Claude also identifies distinctive keywords from the posting that the ontology might miss: brand-new tech, niche tools, soft skills, multi-word phrases not in O*NET. Catches things like specific company-internal tools, freshly-coined ML terms, or domain phrases like "claims substantiation" if not in the supplement.

### Combined score

```
posting_skills = (O*NET skills in posting) ∪ (Claude's distinctive keywords)
resume_skills  = (O*NET skills in resume)  ∪ (Claude's keyword_matches against resume)

ats_score = |posting_skills ∩ resume_skills| / |posting_skills| × 100
            - 5 × format_warnings_count
```

### Why hybrid?

Pure ontology is fully reproducible but bounded by what's in O*NET (~8,800 skills) and can't catch context-aware mentions. Pure LLM extraction is broad but mood-dependent. **Union of both = deterministic baseline + broader coverage.** The score is more stable than pure LLM and broader than pure ontology.

The component breakdown is in `results.json` under `ats_components`:
- `onet_matched` / `onet_missing` — pure-ontology numbers (would be the score if Pass A only)
- `llm_matched` / `llm_missing` — Claude's contribution
- `skills_matched` / `skills_total` — final union (used for the score)

### Caveat: real ATSes vary

This measures *vocabulary overlap*, which is what most older enterprise ATSes do (Workday, Taleo, iCIMS). Modern systems use embeddings (Eightfold, Phenom) or barely auto-score at all (Greenhouse defers to recruiters). Our hybrid score is a reasonable proxy — not a guarantee any specific ATS would give the same number.

## 4. Domain fit score (0–100, embedding similarity — optional)

Cosine similarity between [sentence-transformer](https://www.sbert.net/) embeddings of your resume and the job posting, scaled to 0–100. Captures *topical* similarity even when specific keywords differ ("data pipelines" ≈ "ETL workflows" ≈ "data flow infrastructure").

Requires `sentence-transformers` installed. It is optional because it pulls a large PyTorch/transformers stack; install it with `pip install -r requirements-full.txt` or `pip install -e ".[embeddings]"`. When the dependency is missing, `domain_fit_score` is simply omitted from results.

## Why four different angles?

The scores measure different things and can diverge — that's a feature, not a bug:

- **High qualification, low ATS**: you've done the work but your resume uses different vocabulary than the posting. **Fix:** `python tailor.py`.
- **High ATS, low qualification**: your resume keyword-matches but you don't have the actual experience. Tailoring won't help — be honest about your level.
- **High domain fit, low qualification**: this is your *field* but not this specific role. Could indicate a good company to target with different roles.
- **All four high**: clear apply.

## Per-requirement evidence (the foundation)

For every requirement found in the posting, `results.json` contains:

| Field | Meaning |
|---|---|
| `requirement` | Short paraphrase of what the posting asked for |
| `met` | `true` if your resume clearly demonstrates it, `false` otherwise |
| `confidence` | How clearly the resume supports the assessment: `high` (explicit), `medium` (inferred from adjacent experience), `low` (ambiguous — consider clarifying your resume here) |
| `evidence` | The specific quote or reference from your resume that supports the judgment, or "not mentioned in resume" / "resume shows 3 yrs vs 5+ required" for unmet requirements |

The `met` field directly drives the qualification score (via `met_ratio`). `confidence: low` entries are good candidates to clarify in your resume.

Run `python audit.py <url>` for any job to see the per-requirement evidence AND the full score derivation in one place.

## Threshold tuning

The default thresholds (`qualified ≥ 80`, `stretch ≥ 50`) are judgment calls. To change them, edit the last line of `derive_qualification_score` in `pipeline.py`:

```python
verdict = "qualified" if score >= 80 else "stretch" if score >= 50 else "not_qualified"
```

The penalty weights (`-30` for degree below, `-5` per year short, capped at `-30`) are also at the top of the same function. They're tuned for "honest but not punishing" — you can tighten or loosen them to match your judgment.
