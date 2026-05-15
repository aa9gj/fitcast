# Customizing

Everything's in `config.yaml`. Edit, re-run.

First time? `cp config.example.yaml config.yaml` — your `config.yaml` is git-ignored and yours alone, so editing it never conflicts with `git pull`. The tracked template is `config.example.yaml`; new knobs land there and you copy what you want.

## Filter to fresh jobs only

```yaml
posted_within_hours: 48     # legacy integer form — hours
posted_within_hours: "7d"   # also accepts shorthand strings
posted_within_hours: "24h"
posted_within_hours: "1w"
posted_within_hours: "1d12h"  # composite forms work too
```

Accepts an integer (hours) or a shorthand string: `Xh` / `Xd` / `Xw` for hours / days / weeks. Composite forms like `1d12h` or `2w3d` sum the parts. Case-insensitive on the unit.

Comment out (or remove) the line to disable.

**Caveat for Greenhouse:** the `posted_at` field comes from the `updated_at` API field, which is "last touched" not "first posted." A recruiter editing an old posting bumps the timestamp, so "48h-fresh" is approximate. The Muse's `publication_date` is true post date.

## Filter by keywords

Case-insensitive substring filter, ORed — a job passes if **any** term matches.

```yaml
keywords:
  - data scientist
  - computational biolog
  - regulatory
keyword_match: title    # "title" (default) or "title_and_body"
```

**`keyword_match` controls where terms are matched** — this matters a lot:

| Value | Matches against | Behavior |
|---|---|---|
| `title` (default) | Job **title** only | Precise. A keyword in the title ("Machine Learning Engineer") is a real role signal. Use role-type terms. |
| `title_and_body` | Title **+ full description HTML** | Broad. Job descriptions are 3,000+ words of boilerplate; one generic term (`data`, `evidence`, `regulatory`) appears in a huge fraction of *all* postings, so a multi-keyword OR filter matches ~everything and stops filtering. Only safe with very distinctive terms (`GraphRAG`, `Veeva`, `multi-omics`). |

If you see a log line like `keyword filter (51 terms, scope=title_and_body): 69929 -> 67291` (almost nothing filtered), that's the body-matching trap — switch to `keyword_match: title`.

Empty list (`keywords: []`) = no keyword filter at all (the pipeline warns loudly — it's almost never intended).

To get a personalized starting list from your resume:

```bash
python extract_keywords.py
```

Outputs a YAML block with profile summary, target roles, and recommended keywords aligned to your background. With the default `title` scope, favor the role-type terms it suggests. Copy the `keywords:` portion into config.yaml.

## Filter by location

Three rule lists, all case-insensitive. Mix them however you like:

```yaml
location_filter:
  cities:            # word-boundary match — safe for short tokens
    - Boston
    - New York
    - San Francisco
    - Seattle
  include:           # substring match — good for "remote", country names
    - remote
    - hybrid
    - united states
    - usa
  exclude:           # any match drops the job (wins over cities + include)
    - india
    - philippines
```

**Rules:**

- **`exclude` wins**: if any exclude term substring-matches the location, drop. Always.
- **`cities`** uses **word-boundary matching** (`\b` regex anchors). `"MA"` matches `"Boston, MA"` but NOT `"Manila, Philippines"`. `"SF"` matches `"SF, CA"` but NOT `"Salford, UK"`. Use this for short ambiguous tokens.
- **`include`** uses **substring matching**. Better for longer / unambiguous terms (`remote`, `hybrid`, `united states`).
- A job passes if **either** a `cities` entry **OR** an `include` entry matches.
- All three lists empty / block commented out = no location filtering.

**Caveats** — location strings vary by source:

| Source | Example locations |
|---|---|
| Greenhouse | `"San Francisco, CA"`, `"Remote — US"`, `"Multiple Locations"` |
| Lever | `"Paris"`, `"Remote – Americas"` |
| Ashby | `"Remote"`, `"New York, NY"` |
| The Muse | `"Flexible / Remote"` |

Common gotchas:
- Country code `"us"` as a substring matches `"Austin"`, `"Houston"`, `"Boston"`. Use `"USA"` or `"United States"` instead — or put `"US"` in `cities` for the word-boundary version.
- `"remote"` is universally safe — every source uses it.

## Filter by salary

```yaml
salary_filter:
  min: 100000   # USD per year — drop if posting's max stated salary < this
  max: 250000   # USD per year — drop if posting's min stated salary > this
```

Both bounds optional — set either or both.

How it works:
- Regex-scans the posting title + body for dollar amounts (e.g. `$120,000`, `$150k`, `$90K - $130K`)
- If any are found, compares the posting's salary range (min..max of all values found) against your filter range
- Drops only if the posting's range is **entirely outside** yours (no overlap). Postings whose range overlaps yours pass — they might still hit your target.
- If no salary mentioned anywhere → **pass through** (don't penalize companies that don't disclose)

**Examples:**

| Posting says | Filter `min: 100000, max: 200000` | Result |
|---|---|---|
| `$120k - $180k` | overlap | ✓ pass |
| `$80k - $300k` | overlap (spans yours) | ✓ pass |
| `$50k - $80k` | entirely below min | ✗ drop |
| `$220k - $260k` | entirely above max | ✗ drop |
| (nothing mentioned) | n/a | ✓ pass |

**Numerical salary in results:** every job that survives gets `salary_min_extracted` and `salary_max_extracted` columns populated (when salary was mentioned) — so you can sort `results.csv` by salary, plot it, or use it as a tiebreaker between similarly-scored matches. Empty cells = the posting didn't mention salary. Values are plain integers in USD.

**Caveats:**
- USD-only. Postings in other currencies won't have their numbers matched.
- The regex is conservative: it only matches `$X,XXX[,XXX]` and `$XXk` patterns, filtered to the $30K–$2M range. Numbers outside that range (hourly rates, valuation figures, equity totals) are skipped.
- Salaries embedded in tables that markdown can't preserve will be invisible. Run `python check_resume_format.py` on a sample posting if you suspect extraction issues.
- Not every posting has salary at all. Most Greenhouse/Lever/Ashby boards don't include it. The Muse sometimes does. **Default behavior of "pass through if missing" is conservative** — you'll still see jobs that don't disclose. Set the filter only if you'd rather skip those entirely (delete the `salary_filter` block and the script keeps everything).

To test what your filter catches, run with `--dry-run`:

```bash
python pipeline.py --dry-run
```

The stderr will report `salary filter (>= $100,000 or unstated): 542 -> 287`. If the drop is unexpectedly large, your minimum may be too aggressive.

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

By default, jobs that were successfully analyzed in a prior run are skipped on subsequent runs. This avoids paying to re-analyze the same jobs as you iterate, while keeping jobs that were merely filtered, pre-ranked away, or failed analysis eligible for a future run.

State lives in `seen.json` (gitignored). Each entry: `{title, company, first_seen, last_seen}`. The file is updated only after at least one job analysis succeeds.

To re-analyze everything anyway:

```bash
python pipeline.py --include-seen
```

To start fresh: delete `seen.json`.

## Install Options

Default install keeps setup lighter and omits the optional embedding stack:

```bash
pip install -r requirements.txt
```

To include `domain_fit_score`, install the full requirements file:

```bash
pip install -r requirements-full.txt
```

For development:

```bash
pip install -e ".[dev]"             # tests, no embeddings
pip install -e ".[dev,embeddings]"  # tests + domain_fit_score
```

For a pinned, known-good full development environment:

```bash
pip install -r requirements.lock
```

## Schedule recurring runs

**Recommended: cron.** It survives terminal close, laptop sleep, and reboots; gives you per-run logs in `/var/mail/$USER` and email-on-failure for free; and it's one less Python process to babysit.

A typical setup — daily at 7am, redirecting stderr (the human log) to a dated file:

```cron
# crontab -e
0 7 * * * cd /path/to/fitcast && /path/to/.venv/bin/python pipeline.py >> "logs/$(date +\%F).log" 2>&1
```

Pair with `--notify-webhook` (see next section) to get pinged on Slack/Discord when new high-score jobs land — no need to open `results.csv` after each run.

**Alternative: `--watch` (in-process loop).** Simpler for laptop dev when you don't want to touch `crontab`. Loops forever; press Ctrl-C to stop. Does **not** survive laptop sleep or terminal close, doesn't rotate logs.

```bash
python pipeline.py --watch --interval 24h
```

Interval format: `24h`, `12h`, `6h`, `30m`, `2h30m`. Minimum 60 seconds (to avoid hammering APIs).

## Get notified when new top matches appear

Skip the "open results.csv after each run" step by letting cron + a webhook do it. Add to `config.yaml`:

```yaml
notify:
  webhook_url: "https://hooks.slack.com/services/..."  # Slack / Discord / generic
  min_score: 75       # only notify if any result hits this score or higher
  max_jobs: 5         # cap items in the message body
```

Or override per-run from the CLI: `python pipeline.py --notify-webhook https://...`.

The webhook receives a JSON POST with:

```json
{
  "run_completed_at": "2025-05-14T07:00:12+00:00",
  "new_top_jobs": [
    {"score": 84, "title": "...", "company": "...", "url": "..."}
  ],
  "total_results": 10,
  "total_new_top_jobs": 1
}
```

Slack and Discord both accept this shape via their generic webhook URLs (no extra fields needed for the simplest "X new jobs found" message). For a richer formatted Slack message, wrap a Slack-formatted block in a small relay script.

## Tailor a resume + cover letter for a specific job

After running `pipeline.py`, generate a tailored LaTeX resume and a LaTeX cover letter (with web-searched recent company news) for the jobs you actually want to apply to:

```bash
python tailor.py --top 3                  # top 3 by qualification score
python tailor.py <url>                    # one specific job from results.json
python tailor.py --top 5 --min-score 70   # only above a score floor
python tailor.py --top 3 --no-cover       # tailored resume only
python tailor.py <url> --no-resume        # cover letter only
```

For each job you get three files in `tailored/`:

| File | What it is |
|---|---|
| `<slug>_resume.tex` | Self-contained LaTeX resume — reordered/rephrased to match the posting's vocabulary, never inventing experience |
| `<slug>_cover.tex`  | LaTeX cover letter — opens with a recent specific thing about the company (product launch, funding, paper, partnership) found via web search; ~250-350 words |
| `<slug>_changes.md` | Audit: every material change made to the resume, citing the line in your master resume that justifies it |

**Compile to PDF:**

```bash
pdflatex tailored/<slug>_resume.tex     # standard LaTeX distro (TeX Live, MacTeX)
tectonic tailored/<slug>_resume.tex     # lighter alternative, auto-fetches packages
```

Or upload the `.tex` to https://overleaf.com if you don't have a local LaTeX install.

**Cost:** ~$0.15 per job (resume call ~$0.05 + cover letter call with up to 3 web searches ~$0.10).

**Voice:** Both documents use deslop guidance baked into the prompts — no "I'm excited to apply", no verb-front bullets ("Spearheaded", "Leveraged"), no tricolons in every paragraph, no em-dash overuse. If you spot AI-cliche output, the model is drifting; re-run or open an issue.

**Truth constraint:** The tailoring prompt explicitly forbids inventing skills, inflating years, or fabricating metrics. The cover letter prompt forbids inventing claims about the candidate AND about the company (if a search didn't verify it, it doesn't go in). The `_changes.md` audit lets you spot-check.

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
python scripts/bootstrap_ontologies.py           # uses cached zip if present
python scripts/bootstrap_ontologies.py --force   # re-download
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
