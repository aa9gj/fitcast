# fitcast

> Forecast your fit for jobs.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/aa9gj/fitcast/blob/main/fitcast.ipynb)

A small tool that scrapes public job boards, asks Claude to find the requirements section in each posting, predicts whether your resume qualifies you for the role, scores ATS keyword alignment, and generates tailored resumes for top matches. Outputs ranked CSV/JSON.

## Quick start

**No install? Click the Colab badge above** to run everything in your browser. You'll just need an Anthropic API key (see [What it costs you](#what-it-costs-you)).

The CLI version below requires **Python 3.10+**. If your default `python3` is older, use a newer one explicitly (e.g. `python3.11`, `python3.12`).

```bash
git clone https://github.com/aa9gj/fitcast
cd fitcast

# Recommended: a clean virtualenv on Python 3.10+
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp resume.example.md resume.md
$EDITOR resume.md       # paste your real resume in markdown

export ANTHROPIC_API_KEY=sk-ant-...
python pipeline.py                   # find + score jobs
python tailor.py --top 3             # tailor a resume for your top 3 matches
```

Open `results.csv` in Google Sheets or Excel — the URL column links straight to each job's apply page. Tailored resumes land in `tailored/`.

## What it costs you

There's **no subscription** — the tool is free, you only pay Anthropic for the API calls it makes on your behalf.

**Anthropic API pricing** (per million tokens; ~4 characters = 1 token):

| Model | Input | Output | Used for |
|---|---|---|---|
| **Haiku 4.5** | $1.00 | $5.00 | Cheap pre-rank pass |
| **Sonnet 4.6** | $3.00 | $15.00 | Deep analysis + tailoring (default) |
| **Opus 4.7** | $15.00 | $75.00 | Optional, ~2.5× more nuance on borderline cases |

**Per individual call:**

| Step | Model | Cost per call |
|---|---|---|
| Pre-rank | Haiku 4.5 | ~$0.0007 |
| Deep analyze | Sonnet 4.6 | ~$0.02 – $0.04 |
| Tailor | Sonnet 4.6 | ~$0.04 – $0.07 |

**Per typical run** (default config — pre-rank 100 candidates, deep-analyze top 10, tailor top 3):

| Step | Subtotal |
|---|---|
| Pre-rank 100 candidates | $0.07 |
| Deep-analyze 10 jobs | $0.30 |
| Tailor 3 top matches | $0.15 |
| **End-to-end** | **~$0.50 per run** |

**Per month** if you're actively job-hunting (3 runs/week): **~$6/month**.

For context: LinkedIn Premium Career is $40/month; most "AI resume tailoring" SaaS tools are $20–$60/month. You'd break even versus one month of LinkedIn Premium after roughly 80 runs.

**Switching to Opus 4.7** multiplies the deep-analyze and tailor costs by ~2.5× — about $1.20/run, ~$15/month at 3 runs/week.

**Account setup:** Anthropic requires a minimum **$5 credit** to start using the API. That covers ~10 full runs at default settings — enough to decide if the tool is worth it before adding more.

## What you get

`results.csv` is sorted by qualification score (best matches first). Key columns:

| Column | What's in it |
|---|---|
| `score` | 0–100 qualification fit — does the candidate actually meet the requirements? |
| `verdict` | `qualified` (≥80) / `stretch` (60–79) / `not_qualified` (<60) |
| `ats_score` | 0–100 keyword alignment with the posting (separate from `score` — can diverge) |
| `domain_fit_score` | 0–100 topical similarity (resume × posting embedding cosine). Empty if `sentence-transformers` isn't installed. |
| `title`, `company`, `location` | Self-explanatory |
| `url` | **Direct link to apply** — opens in your browser |
| `posted_at` | When the job was posted/updated |
| `prerank_score` | 0–10 cheap-pass relevance score (only when prerank is enabled) |
| `missing` | Requirements you don't meet |
| `matched` | Requirements you do meet |
| `ats_keyword_gaps` | Keywords from the posting missing from your resume — ATS optimization targets |
| `ats_keyword_matches` | Keywords from the posting that are already in your resume |
| `rationale` | Claude's 2-3 sentence explanation |
| `requirements_text` | Verbatim requirements section pulled from the posting |

`results.json` has the same data plus:
- Full posting text (used by `tailor.py` and `audit.py`)
- A `requirements_evidence` array — one entry per requirement with `met`, `confidence`, and a specific quote/reference from your resume
- `score_components`, `ats_components`, `breakdown` — the inputs that produced each score (see [How scoring works](#how-scoring-works))

Use [`audit.py`](#audit-a-jobs-score) to print all of this human-readably for any job.

## How scoring works

Each job gets **four scores**. Three are *derived deterministically* from Claude's extraction — the math is auditable, not a black-box vibe number. The fourth uses local embeddings. Claude's role is extraction (find requirements, judge each one, list keywords); the pipeline does the arithmetic.

### 1. Pre-rank score (0–10, Haiku 4.5 — LLM judgment)

A cheap one-shot relevance score generated *before* the expensive deep analysis. Asks: "given the candidate's resume, how plausibly relevant is this job at all?"

- **0–3**: clearly unrelated
- **4–6**: tangentially related — maybe worth considering
- **7–10**: clearly relevant

Only jobs scoring ≥ `prerank.threshold` (default 5) survive to deep analysis. Raise the threshold to 7+ if you're seeing too many irrelevant jobs in your `results.csv`.

This is the one score still pure LLM judgment, because it runs on 100+ candidates and needs to be cheap.

### 2. Qualification score (0–100, derived from evidence)

Claude extracts per-requirement evidence and quantitative inputs; the pipeline DERIVES the score using a transparent formula:

```
base_score = (requirements_met / requirements_total) × 100

degree_penalty = -30 if resume degree is below the posting's requirement, else 0
years_penalty  = -5 per year short on experience, capped at -30

score = clamp(base_score + degree_penalty + years_penalty, 0, 100)
verdict = "qualified" if score >= 80 else "stretch" if score >= 60 else "not_qualified"
```

Every input is in `results.json` under `score_components` and `breakdown`:

| Field | Example |
|---|---|
| `requirements_met` / `requirements_total` | 5 / 9 |
| `met_ratio` | 0.56 |
| `base_score` | 56 |
| `years_required` / `years_resume_estimated` | 5 / 3 |
| `years_penalty` | -10 |
| `degree_required` / `degree_resume` / `degree_match` | "PhD" / "PhD" / "meets_or_exceeds" |
| `degree_penalty` | 0 |
| Final `score` | 46 → `stretch` |

You can hand-check any score: "5/9 = 56 base, minus 10 for years, equals 46 → stretch." Run `python audit.py <url>` to print this breakdown for any job.

### 3. ATS score (0–100, derived from keyword extraction)

Claude extracts which of the posting's important keywords DO and DON'T appear in your resume. The pipeline computes:

```
ats_score = (keywords_matched / (keywords_matched + keyword_gaps)) × 100
            - 5 × format_warnings_count
```

This is closer to what real ATS systems actually do (exact keyword matching with skill normalization) than asking an LLM to estimate vocabulary similarity. The math is in `results.json` under `ats_components`.

### 4. Domain fit score (0–100, embedding similarity — optional)

Cosine similarity between [sentence-transformer](https://www.sbert.net/) embeddings of your resume and the job posting, scaled to 0–100. Captures *topical* similarity even when specific keywords differ ("data pipelines" ≈ "ETL workflows" ≈ "data flow infrastructure").

Requires `sentence-transformers` installed (~500MB; included in `requirements.txt` by default — comment out the line to skip the install). When the dep is missing, `domain_fit_score` is simply omitted from results.

### Why four different angles?

The scores measure different things and can diverge — that's a feature, not a bug:

- **High qualification, low ATS**: you've done the work but your resume uses different vocabulary than the posting. **Fix:** `python tailor.py`.
- **High ATS, low qualification**: your resume keyword-matches but you don't have the actual experience. Tailoring won't help — be honest about your level.
- **High domain fit, low qualification**: this is your *field* but not this specific role. Could indicate a good company to target with different roles.
- **All four high**: clear apply.

### Per-requirement evidence (the foundation)

For every requirement found in the posting, `results.json` contains:

| Field | Meaning |
|---|---|
| `requirement` | Short paraphrase of what the posting asked for |
| `met` | `true` if your resume clearly demonstrates it, `false` otherwise |
| `confidence` | How clearly the resume supports the assessment: `high` (explicit), `medium` (inferred from adjacent experience), `low` (ambiguous — consider clarifying your resume here) |
| `evidence` | The specific quote or reference from your resume that supports the judgment, or "not mentioned in resume" / "resume shows 3 yrs vs 5+ required" for unmet requirements |

The `met` field directly drives the qualification score (via `met_ratio`). `confidence: low` entries are good candidates to clarify in your resume.

Run `python audit.py <url>` for any job to see the per-requirement evidence AND the full score derivation in one place.

## Customize

Everything's in `config.yaml`. Edit, re-run.

**Filter to fresh jobs only:**

```yaml
posted_within_hours: 48   # only show jobs from the last 48 hours
```

(Comment out or remove the line to disable.)

**Filter by keywords** (case-insensitive substring on title + body):

```yaml
keywords:
  - data
  - regulatory
  - product manager
```

Empty list = no keyword filter.

**Pre-rank a larger pool with cheap Haiku** (recommended; on by default):

```yaml
prerank:
  enabled: true
  threshold: 5         # 0-10 scale; jobs below this are dropped
  max_candidates: 100  # cap the pre-rank pool to bound cost
```

This lets you scan up to 100 candidates per run for a few cents (Haiku 4.5), then deep-analyze just the most relevant `max_jobs`. Set `enabled: false` to skip.

**Pick how many jobs to deep-analyze** (the main cost lever):

```yaml
max_jobs: 10
```

**Pick your sources.** The pipeline pulls from two at once and dedupes:

- `greenhouse:` — hand-picked company boards (highest-quality data, verbatim full job descriptions)
- `muse:` — a free public aggregator (no signup; you filter by category, level, location)

Comment out either block to disable. See [Data sources](#data-sources) for the reasoning.

## Tailor your resume for top matches

```bash
python tailor.py --top 3                     # top 3 from results.json
python tailor.py --top 5 --min-score 70      # only matches above qualification score 70
python tailor.py <job-url-from-results.json> # one specific job
```

Reads `results.json`, takes your master `resume.md`, and asks Claude to rewrite each as a tailored version that:

- Leads with the most-relevant experience first
- Uses the posting's vocabulary where you genuinely have the experience (helps with ATS keyword density)
- Drops bullets clearly irrelevant to that specific job
- Adds a "Changes Summary" at the bottom showing every edit and why it's honest

**Important:** the prompt explicitly forbids inventing skills, inflating years, or fabricating achievements. Tailoring is *emphasis and vocabulary*, not fiction. Recruiters spot fabrication instantly.

Output goes to `tailored/<company>_<title>.md`. To convert to DOCX for ATS upload:

```bash
pandoc tailored/foo.md -o foo.docx
```

(Most ATS uploaders prefer DOCX or PDF over Markdown.)

Cost: ~$0.05 per tailored resume on Sonnet 4.6.

## Audit a job's score

After running `pipeline.py`, you can re-analyze any specific job in verbose mode to see exactly how it was judged:

```bash
python audit.py <job-url-from-results.json>
```

Prints (a) Claude's full reasoning chain, (b) per-requirement breakdown showing which lines from your resume were used as evidence for each requirement, (c) ATS keyword analysis with matches/gaps, and (d) the verbatim requirements section from the posting.

Use this when:
- A score surprises you and you want to understand why
- You suspect Claude misread your resume on a specific requirement
- You're deciding whether to tailor for a `stretch` match — see exactly what's missing first

Cost: ~$0.04 per audit (uses higher effort than the main run for more detailed reasoning).

## Auto-extract keywords from your resume

Tired of curating the `keywords:` filter manually? Let Claude propose them from your resume:

```bash
python extract_keywords.py
```

Outputs a YAML block of 10–20 high-signal keywords (technologies, domains, role types) ready to paste into `config.yaml`'s `keywords:` section. Edit, prune, or extend.

Cost: ~$0.005 per run.

## Track which jobs you've applied to

```bash
python track.py mark https://job-boards.greenhouse.io/recursion/jobs/12345
python track.py list                    # show everything, newest first
python track.py list rejected           # filter by status
python track.py remove https://...
```

Once a job is tracked, future runs of `pipeline.py` skip it automatically — no wasted API calls re-analyzing things you've already acted on.

Status is free-form. Conventional values: `interested`, `applied`, `phone_screen`, `interview`, `offer`, `rejected`, `withdrew`.

## Expand your company list (optional)

`config.yaml` ships with ~8 hand-picked Greenhouse companies. To add hundreds more:

```bash
python bootstrap_companies.py           # preview
python bootstrap_companies.py --write   # save to companies.bootstrap.yaml
```

Pulls from [SimplifyJobs](https://github.com/SimplifyJobs)'s community-maintained listings and extracts every Greenhouse slug (usually 500–1000). The pipeline picks them up automatically on the next run.

Caveat: SimplifyJobs is intern/new-grad focused, so the *roles* it tracks won't match a senior search — but the *companies* are the same companies that also post senior roles.

Re-run periodically to refresh. Delete `companies.bootstrap.yaml` to revert.

## How it works

1. **Scrape** — pulls jobs from Greenhouse + The Muse in parallel.
2. **Filter** — date / keyword / already-applied filters; dedupes by URL.
3. **Pre-rank (optional)** — Haiku 4.5 cheaply scores each candidate 0–10 for resume relevance; takes the top `max_jobs`.
4. **Extract** — Sonnet 4.6 (adaptive thinking, structured output) extracts the requirements section, per-requirement evidence with confidence levels, quantitative inputs (years/degree), and ATS keyword data. Claude does extraction + judgment per item, *not* score-picking.
5. **Score** — derive qualification score + verdict from the extraction (formulas in [How scoring works](#how-scoring-works)), derive ATS score from keyword overlap, compute domain fit from sentence-transformer embeddings.
6. **Rank & write** — sort by qualification score, write `results.csv` + `results.json`.
7. **Tailor (separate command)** — `tailor.py` rewrites your resume per-job, staying strictly truthful.

### Choice of model

**Default is `claude-sonnet-4-6`** for deep analysis and tailoring. The pipeline is structured extraction + ranked match + careful rewriting — all well within Sonnet's wheelhouse. Opus 4.7 adds nuance on borderline cases but costs ~2.5× more per token.

To switch deep-analysis to Opus, change `MODEL` at the top of `pipeline.py` and `tailor.py` to `claude-opus-4-7`.

The pre-rank pass uses **`claude-haiku-4-5`** (cheapest, fastest model). The pre-rank prompt is intentionally simple — just a 0–10 relevance score — so Haiku is appropriate.

## Data sources

Two sources, both enabled by default. Results merged + deduplicated by URL.

### Greenhouse (curated boards)

Verbatim full job descriptions straight from each company's ATS — no scraping or aggregator middleware. The catch: there's no public directory of Greenhouse boards, so slugs have to be discovered one company at a time. The seed list is biotech/health-AI focused; use `bootstrap_companies.py` to expand it.

### The Muse (aggregator)

Queries [The Muse's public job API](https://www.themuse.com/developers/api/v2). No signup, no API key. You specify categories, levels, and locations.

Tradeoff: descriptions can be slightly thinner than direct-from-Greenhouse, but for predict-the-requirements + qualify-or-not it's almost always enough text. And you trade list-maintenance work for a few config knobs.

### Why both?

Greenhouse gives you the highest-quality data for specific companies you've targeted; The Muse gives you breadth across companies you haven't heard of yet. Together — with dedup — you get both, with no list to maintain beyond your ~10 favorite companies.

## License

MIT — see [LICENSE](LICENSE).
