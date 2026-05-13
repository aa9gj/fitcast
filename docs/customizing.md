# Customizing

Everything's in `config.yaml`. Edit, re-run.

## Filter to fresh jobs only

```yaml
posted_within_hours: 48   # only show jobs from the last 48 hours
```

Comment out (or remove) the line to disable.

**Caveat for Greenhouse:** the `posted_at` field comes from the `updated_at` API field, which is "last touched" not "first posted." A recruiter editing an old posting bumps the timestamp, so "48h-fresh" is approximate. The Muse's `publication_date` is true post date.

## Filter by keywords

Case-insensitive substring match on title + body:

```yaml
keywords:
  - python
  - regulatory
  - product manager
```

Empty list (`keywords: []`) = no keyword filter.

To get a personalized starting list from your resume:

```bash
python extract_keywords.py
```

Outputs a YAML block with profile summary, target roles, and recommended keywords aligned to your background. Copy the `keywords:` portion into config.yaml.

## Pre-rank a larger pool with cheap Haiku

```yaml
prerank:
  enabled: true
  threshold: 5         # 0-10 scale; jobs below this are dropped
  max_candidates: 100  # cap the pre-rank pool to bound cost
```

This lets you scan up to 100 candidates per run for ~$0.07 (Haiku 4.5), then deep-analyze just the most relevant `max_jobs`. Set `enabled: false` to skip.

Raise the `threshold` if you're seeing too many irrelevant jobs in `results.csv` after deep analysis.

## Pick how many jobs to deep-analyze

The main cost lever:

```yaml
max_jobs: 10
```

CLI override: `python pipeline.py --max-jobs 3` for a cheap test (~$0.10).

## Pick your sources

Four sources, all on by default. Comment out a top-level block to disable that source. See [docs/sources.md](sources.md) for what each is.

## Track applications

```bash
python track.py mark <job-url>           # default: status="applied"
python track.py mark <job-url> phone_screen
python track.py list                     # show everything, newest first
python track.py list rejected            # filter by status
python track.py remove <job-url>
```

Once a job is in `applied.json`, future `pipeline.py` runs skip it automatically (saves API spend on jobs you've already acted on).

Status is free-form. Conventional values: `interested`, `applied`, `phone_screen`, `interview`, `offer`, `rejected`, `withdrew`.

## Skip jobs you've already seen (across runs)

By default, jobs that appeared in any prior run are silently skipped on subsequent runs. This avoids paying to re-analyze the same jobs as you iterate.

State lives in `seen.json` (gitignored). Each entry: `{title, company, first_seen, last_seen}`.

To re-analyze everything anyway:

```bash
python pipeline.py --include-seen
```

To start fresh: delete `seen.json`.

## Watch mode

For "run this every day" without managing cron:

```bash
python pipeline.py --watch --interval 24h
```

Loops forever. Each run writes `results.csv` (overwriting). Press Ctrl-C to stop.

Interval format: `24h`, `12h`, `6h`, `30m`, `2h30m`. Minimum 60 seconds (to avoid hammering APIs).

For production use, **prefer cron**:

```cron
# /etc/crontab
0 7 * * * cd /path/to/fitcast && /path/to/.venv/bin/python pipeline.py
```

Cron gives you logs and email-on-failure. Watch mode is simpler for dev/testing.

## A/B test resume versions

Iterating on resume wording? Compare multiple versions on the same jobs:

```bash
python compare_resumes.py --top 5 resume_v1.md resume_v2.md
python compare_resumes.py --url <url> resume_v1.md resume_v2.md resume_v3.md
```

Each (job × resume) pair is a Claude call, so 3 jobs × 2 resumes = ~$0.15.

Output is a comparison table per job, with the highest-scoring resume marked with ★.

## Audit a job's score

For any job in `results.json`, see the full math:

```bash
python audit.py <url>
```

Prints (a) Claude's reasoning chain, (b) per-requirement breakdown showing which lines from your resume were used as evidence, (c) the score-derivation math, (d) ATS skills matched/missing, (e) the verbatim requirements section.

Cost: ~$0.04 per audit (uses higher effort than the main run).

## Check whether your real PDF/DOCX would survive an ATS parser

The pipeline scores using `resume.md`, but actual ATSes parse PDF/DOCX files where extraction often fails (tables jumbled, multi-column layouts read top-to-bottom). Drop a real `resume.pdf` or `resume.docx` into the project directory and run:

```bash
python check_resume_format.py
```

Detects: content loss, missing section headings, header/footer leakage, multi-column issues, encoding glitches, embedded tables. Saves the actual extracted text to `resume.extracted.<format>.txt` for review.

One-time diagnostic, doesn't affect any pipeline scores.

## Refresh the O*NET skill catalog

The catalog ships pre-built (`data/skills.json`). To refresh after O*NET releases a new version (~every 6 months):

```bash
python bootstrap_ontologies.py           # uses cached zip if present
python bootstrap_ontologies.py --force   # re-download
```

To extend the supplement with terms O*NET still misses, edit `data/skills_supplement.txt` (one term per line, comments with `#`) and re-run bootstrap.

## Switch the LLM model

Edit `MODEL` at the top of `pipeline.py` (and `tailor.py` for tailoring):

```python
MODEL = "claude-sonnet-4-6"   # default
# MODEL = "claude-opus-4-7"   # ~2.5× cost, marginally more nuance
```

The pre-rank model is separate and stays at Haiku 4.5 (cheapest):

```python
PRERANK_MODEL = "claude-haiku-4-5"
```

## Tune scoring thresholds

In `pipeline.py`, the `derive_qualification_score` function has the formula:

```python
verdict = "qualified" if score >= 80 else "stretch" if score >= 50 else "not_qualified"
```

The penalty constants (`-30` for degree below, `-5` per year short, capped at `-30`) are at the top of the same function. All judgment calls — adjust to match your sense of "qualified" vs "stretch" vs "not_qualified."

Re-run tests after changing: `pytest tests/test_scoring.py`.
