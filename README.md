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

> *Note on prompt caching:* the pipeline uses Anthropic's prompt-caching API, but a typical resume + system prompt (~1,500 tokens) sits under the model's minimum cacheable-prefix size, so the cache is a silent no-op for most users. Affects cost, not correctness.

## What you get

`results.csv` is sorted by qualification score (best matches first). Key columns:

| Column | What's in it |
|---|---|
| `score` | 0–100 qualification fit — does the candidate actually meet the requirements? |
| `verdict` | `qualified` / `stretch` / `not_qualified` |
| `ats_score` | 0–100 keyword alignment with the posting (separate from `score` — can diverge) |
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

`results.json` has the same data plus full posting text (used by `tailor.py`).

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
4. **Deep analyze** — Sonnet 4.6 (adaptive thinking, structured output) returns the verbatim requirements section, qualification verdict + score + matched/missing list, and ATS keyword assessment.
5. **Rank & write** — sorts by qualification score, writes `results.csv` + `results.json`.
6. **Tailor (separate command)** — `tailor.py` rewrites your resume per-job, staying strictly truthful.

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
