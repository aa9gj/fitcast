# fitcast

> Forecast your fit for jobs.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/aa9gj/fitcast/blob/main/fitcast.ipynb)

A small CLI that scrapes public job boards, asks Claude to find the requirements section in each posting, and predicts whether your resume qualifies you for the role — with a transparent score derivation, ATS-keyword matching against the O*NET skill ontology, and an option to generate per-job tailored resumes.

**Access note:** the quickstart and Colab badge work for anyone once this repository is public. While the repo is private, clone/Colab access requires GitHub access to `aa9gj/fitcast`; use the local quickstart from an authenticated checkout.

## Quick start

Requires **Python 3.10+**.

```bash
git clone https://github.com/aa9gj/fitcast
cd fitcast

# Recommended: a clean virtualenv on Python 3.10+
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional: add local embedding-based domain_fit_score
# pip install -r requirements-full.txt

cp resume.example.md resume.md
$EDITOR resume.md       # paste your real resume in markdown

export ANTHROPIC_API_KEY=sk-ant-...
python pipeline.py
```

Open `results.csv` in Google Sheets — sorted by score, URLs are clickable apply links.

For a reproducible full development environment, use `pip install -r requirements.lock`. For editable installs, `pip install -e ".[dev]"` installs the test dependencies, and `pip install -e ".[dev,embeddings]"` also enables `domain_fit_score`.

## Configure your search

The only file you typically edit is [`config.yaml`](config.yaml). Four knobs cover almost every use case:

```yaml
# Where you want jobs to be:
location_filter:
  cities: [NC, Raleigh, Durham, Charlotte]   # word-boundary match (safe for short codes like "NC")
  include: [remote, hybrid]                  # substring match (good for general terms)
  exclude: [india, philippines]              # any match drops the job (wins over include)

# Salary range (USD/year). Postings without a stated salary pass through unless filtered explicitly.
salary_filter:
  min: 85000     # drop if posting's stated max is below this
  max: 150000

# How fresh the posting needs to be. Accepts "24h", "7d", "1w", "1d12h", or an integer of hours.
posted_within_hours: "24h"

# Which companies to scrape:
greenhouse:
  companies: [flatironhealth, freenome, ...]   # hand-pick known companies
```

**Don't want to research company slugs?** Run `python bootstrap_companies.py --write` once. It pulls **~1,500 public companies** across Greenhouse / Lever / Ashby from the community-maintained SimplifyJobs lists, merged automatically into your next run. Your `location_filter` and `salary_filter` then narrow that broad pool to what matters to you. Free, ~30s.

So you have two equivalent paths to broad coverage:
- **Hand-curated:** list specific company slugs in `config.yaml` (best when you have a target list)
- **Bootstrapped:** run `bootstrap_companies.py --write` once for the long tail (best when you don't)

Both approaches respect the same `location_filter` / `salary_filter` / `keywords` — they only change *which boards get scraped*.

For deeper customization (the Muse aggregator's categories/levels, pre-rank settings, webhook notifications), see [docs/customizing.md](docs/customizing.md).

## Reading your results

Each row in `results.csv` has a `verdict` column with one of three values:

| Verdict | Score | What it means |
|---|---|---|
| `qualified` | 80–100 | You meet ~all stated requirements. Apply with the standard resume. |
| `stretch` | 50–79 | You meet most requirements with some gaps. Common — most results for any specific role land here. Apply with a tailored resume (`python tailor.py --top 3`) to bridge the gap. |
| `not_qualified` | 0–49 | Significant gaps. Either the posting isn't a good fit, or your resume isn't surfacing the right experience. |

**Mostly `stretch` is normal.** Job postings list a wish-list of ideal requirements; almost no candidate matches them all. A `stretch` (≥50%) means it's worth applying — *especially* if `ats_score` is also high. Don't read `stretch` as "skip this one."

**Common pattern: `stretch` verdict + low `ats_score` (< 60).** You probably have the experience, but your resume uses different vocabulary than the posting. Fix:
```bash
python tailor.py <url>      # rewrites your resume to mirror the posting's wording, never inventing experience
```
Tailoring is the #1 reason first-run results feel discouraging.

**Got fewer results than `max_jobs`?** Your filters narrowed the pool below the requested count. Most common causes, in order:
1. `posted_within_hours` too tight — try `"7d"` instead of `"24h"`
2. Empty scrape pool — no companies configured AND no bootstrap. Run `python bootstrap_companies.py --write`.
3. `keywords` too narrow — run `python extract_keywords.py` for resume-tuned suggestions you can paste into config.yaml
4. `location_filter.cities` missing variants — add full state name + abbreviation + nearby cities

Each filter prints a `N -> M` line in stderr during the run (e.g. `salary filter (>= $85,000 or unstated): 287 -> 142`) so you can see where the pool shrinks.

## What you get

Each run writes `results.csv` (sorted by qualification score) with columns: `score`, `verdict`, `ats_score`, `title`, `company`, `url`, `posted_at`, `missing` (requirements you don't meet), `matched`, `ats_skills_missing` (skills to add to your resume), `requirements_text` (verbatim from posting), and more. An optional `domain_fit_score` column is also populated when the embeddings extra is installed — see [docs/scoring.md](docs/scoring.md#optional-domain-fit-score) for details.

`results.json` adds the full per-requirement evidence chain and score-derivation breakdown — what `audit.py` prints in human-readable form.

## Commands

| Command | What it does | Cost |
|---|---|---|
| `python pipeline.py` | Scrape + score + write `results.csv` | ~$0.30/run |
| `python audit.py <url>` | Show the full score math + evidence for one job | ~$0.04 |
| `python tailor.py --top 3` | Tailored LaTeX resume + cover letter (web-searches recent company news) per top match. Add `--format markdown` for .md output (no LaTeX install needed). | ~$0.15 each |
| `python check_resume_format.py` | Test your real PDF/DOCX against an ATS parser | $0 (local) |
| `python extract_keywords.py` | Get personalized search keywords from your resume | ~$0.02 |
| `python track.py mark <url>` | Mark a job as applied (auto-skip in future runs) | $0 |
| `python compare_resumes.py r1.md r2.md --top 3` | A/B test two resume versions on the same jobs | ~$0.15 |
| `python bootstrap_companies.py --write` | Add hundreds of Greenhouse + Lever + Ashby companies from SimplifyJobs | $0 (download only) |
| `python scripts/bootstrap_ontologies.py` | Refresh the O*NET skill catalog (maintenance — once per O*NET release) | $0 (download only) |
| `./smoke_test.sh [--paid]` | Verify every component end-to-end (~$0.20 with `--paid`) | $0 by default |

Pipeline flags: `--dry-run`, `--max-jobs N`, `--include-seen`, `--notify-webhook URL`. For unattended runs, **prefer cron** over `--watch` — see [docs/customizing.md](docs/customizing.md#schedule-recurring-runs). Full flag list: `python pipeline.py --help`.

## Cost summary

~**$10/month** at 3 runs/week (Sonnet 4.6 default, including tailored resume + cover letter for top 3). Compare LinkedIn Premium at $40/month. Anthropic requires a $5 minimum credit to start. Full breakdown: [docs/cost.md](docs/cost.md).

## Documentation

- **[How scoring works](docs/scoring.md)** — the math behind every number (qualification, ATS, domain fit, pre-rank). Read this if you want to understand or defend a score.
- **[Architecture](docs/architecture.md)** — pipeline design, choice of model, the role of each component.
- **[Data sources](docs/sources.md)** — Greenhouse / Lever / Ashby / The Muse, the bootstrap script, where to find slugs.
- **[Customizing](docs/customizing.md)** — config knobs, watch mode, application tracking, advanced usage.
- **[Cost details](docs/cost.md)** — Anthropic pricing, per-call costs, monthly estimates.

## Run the tests

```bash
pip install -e ".[dev]"
pytest tests/
```

149 tests covering the score-derivation functions, source validation, state handling, location/salary/time-window filters, skill extractor, scrape orchestration (HTTP mocked), config schema validation, webhook notifications, and the SimplifyJobs slug bootstrap. All pure Python — no API calls.

CI runs the syntax check and tests on Python 3.10, 3.11, and 3.12 via GitHub Actions.

## License

[**PolyForm Noncommercial License 1.0.0**](LICENSE) — you may use, modify, and share this software for any **noncommercial purpose** (personal use, research, education, nonprofit organizations, hobby projects). Commercial use is **not permitted** without separate arrangement with the author.

This is a [source-available](https://en.wikipedia.org/wiki/Source-available_software) license, not an OSI-approved open-source license. The source is readable, forkable for noncommercial use, and you're welcome to contribute back — but you can't repackage and sell it.
