# Data sources

The pipeline supports four sources, all enabled together by default. Enable any combination you want; a config with only Lever, only Ashby, only Greenhouse, or only The Muse is valid. Results are merged and deduplicated by URL.

| Source | API base | What it is | How to find slugs |
|---|---|---|---|
| **Greenhouse** | `boards-api.greenhouse.io/v1/boards/<slug>/jobs` | Hand-picked company boards. Highest-quality data — verbatim full job descriptions straight from each company's ATS. | `boards.greenhouse.io/<slug>` in browser |
| **Lever** | `api.lever.co/v0/postings/<slug>?mode=json` | Same idea as Greenhouse. Many AI/ML companies use Lever (Mistral, Cohere historically). | `jobs.lever.co/<slug>` in browser |
| **Ashby** | `api.ashbyhq.com/posting-api/job-board/<slug>` | Same idea. Many newer YC-era startups (Linear, Ramp). | `jobs.ashbyhq.com/<slug>` in browser |
| **The Muse** | `themuse.com/api/public/jobs?...` | Public aggregator API. No signup, no slug list — query by category/level/location. | N/A — uses query filters |

The script silently skips any slug that 404s, so feel free to over-include in your config.

## config.yaml schema

```yaml
greenhouse:
  companies:
    - recursionpharmaceuticals
    - ginkgobioworks
    # ...

lever:
  companies:
    - mistral
    # ...

ashby:
  companies:
    - linear
    # ...

muse:
  categories:
    - Data and Analytics
    - Healthcare
    - Project Management
    - Science and Engineering
  levels:
    - Senior Level
    - Mid Level
  locations:
    - Flexible / Remote
  max_pages: 2
```

Comment out any block to disable that source. At least one of `greenhouse.companies`, `lever.companies`, `ashby.companies`, or `muse` must be enabled.

## Discovering slugs

There's no public directory of who uses which ATS. Slugs have to be found one company at a time. Easiest method: visit a company's careers page in your browser and look at where the "Apply" button takes you.

| URL pattern in browser | ATS | Slug |
|---|---|---|
| `boards.greenhouse.io/<slug>` | Greenhouse | the part after `.io/` |
| `job-boards.greenhouse.io/<slug>` | Greenhouse | the part after `.io/` |
| `jobs.lever.co/<slug>` | Lever | the part after `.co/` |
| `jobs.ashbyhq.com/<slug>` | Ashby | the part after `.com/` |

## Auto-expanding the Greenhouse list

`python bootstrap_companies.py --write` pulls a community-maintained list ([SimplifyJobs](https://github.com/SimplifyJobs)) and extracts every Greenhouse slug it can find — usually 500–1000 companies. The pipeline picks them up automatically on the next run.

> Don't confuse it with `bootstrap_ontologies.py` — that's a *different* script that builds the O*NET skill catalog (`data/skills.json`) used for ATS scoring. Same naming convention, different purposes:
> - `bootstrap_companies.py` → adds *Greenhouse company slugs* (for scraping)
> - `bootstrap_ontologies.py` → builds the *O*NET skill catalog* (for ATS scoring)

```bash
python bootstrap_companies.py           # preview slugs
python bootstrap_companies.py --write   # save to companies.bootstrap.yaml
```

Caveat: SimplifyJobs is intern/new-grad focused, so the *roles* won't match a senior search — but the *companies* are the same companies that also post senior roles on those boards.

Re-run periodically to refresh. Delete `companies.bootstrap.yaml` to revert.

There's no equivalent bootstrap for Lever/Ashby yet (could be added — same architecture). For those, hand-curate.

## Why these four?

- **Greenhouse + Lever + Ashby**: Cover most of the modern tech / biotech / startup hiring. Each has thousands of customer companies. Combined, you can hand-pick whichever ones you'd actually want to work at.
- **The Muse**: Aggregator covering many companies you don't know about. Lower-quality job descriptions on average (sometimes shorter than going direct to the company's ATS) but broader coverage with no slug-curation work.

## What about LinkedIn / Indeed?

They block scrapers aggressively and require auth. Their TOS forbids automated access. We don't try.

If you want broader aggregator coverage beyond The Muse, options are:
- **Adzuna API** — free tier 1000 calls/month, broad coverage including biotech/health
- **JSearch via RapidAPI** — wraps LinkedIn/Indeed/ZipRecruiter, paid

These would each be ~50 lines of code to add. Open a PR if you want one.
