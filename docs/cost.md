# Cost details

You pay only for Anthropic API calls — there's no subscription, the tool itself is free.

## Anthropic pricing (per million tokens)

| Model | Input | Output | Used for |
|---|---|---|---|
| **Haiku 4.5** | $1.00 | $5.00 | Cheap pre-rank pass |
| **Sonnet 4.6** | $3.00 | $15.00 | Deep analysis + tailoring (default) |
| **Opus 4.7** | $15.00 | $75.00 | Optional, ~2.5× more nuance on borderline cases |

A "token" is roughly 4 characters of English text.

## Per-call cost

| Step | Model | Cost per call |
|---|---|---|
| Pre-rank | Haiku 4.5 | ~$0.0007 |
| Deep analyze | Sonnet 4.6 | ~$0.025–$0.045 |
| Tailor | Sonnet 4.6 | ~$0.04–$0.07 |
| Audit | Sonnet 4.6 (high effort) | ~$0.04 |
| Extract keywords | Sonnet 4.6 | ~$0.02 |
| Compare resumes | Sonnet 4.6 | ~$0.025 per (job × resume) pair |

## Per typical run

Default config (pre-rank 100 candidates, deep-analyze top 10, tailor top 3):

| Step | Subtotal |
|---|---|
| Pre-rank 100 candidates | $0.07 |
| Deep-analyze 10 jobs | $0.30 |
| Tailor 3 top matches | $0.15 |
| **End-to-end** | **~$0.50 per run** |

## Per month

If you actively job-hunt — 3 runs/week:

**~$6/month**

For context:
- LinkedIn Premium Career: $40/month
- Most "AI resume tailoring" SaaS tools: $20–$60/month

You'd break even versus one month of LinkedIn Premium after roughly 80 runs.

## Switching to Opus 4.7

Multiplies the deep-analyze and tailor costs by ~2.5×: about $1.20/run, ~$15/month at 3 runs/week.

When to switch: borderline cases where you want maximum nuance. The qualification thresholds (≥80 / ≥50) are robust enough that Sonnet rarely changes the top-of-list ordering vs Opus.

## Account setup

Anthropic requires a minimum **$5 credit** to start using the API. That covers ~10 full runs at default settings — enough to decide if the tool is worth your while.

Get an API key at https://console.anthropic.com/settings/keys.
