# fitcast

> Forecast your fit for jobs.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/aa9gj/fitcast/blob/main/fitcast.ipynb)

A small CLI that scrapes public job boards, asks Claude to find the requirements section in each posting, and predicts whether your resume qualifies you for the role — with a transparent score derivation, ATS-keyword matching against the O*NET skill ontology, and an option to generate per-job tailored resumes.

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

146 tests covering the score-derivation functions, source validation, orchestration, bootstrap helpers, state handling, location/salary/time-window filters, and the skill extractor. All pure Python — no API calls.

CI runs the syntax check and tests on Python 3.10, 3.11, and 3.12 via GitHub Actions.

## License

[**PolyForm Noncommercial License 1.0.0**](LICENSE) — you may use, modify, and share this software for any **noncommercial purpose** (personal use, research, education, nonprofit organizations, hobby projects). Commercial use is **not permitted** without separate arrangement with the author.

This is a [source-available](https://en.wikipedia.org/wiki/Source-available_software) license, not an OSI-approved open-source license. The source is readable, forkable for noncommercial use, and you're welcome to contribute back — but you can't repackage and sell it.
