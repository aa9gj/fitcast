# fitcast — web (showcase + live demo)

A Next.js app that doubles as a portfolio case study for the [fitcast](https://github.com/aa9gj/fitcast)
CLI and a working **live demo** of its single-job scoring.

It is self-contained: the Python project is untouched. Everything here lives in `web/`.

## What it is

- **Showcase** (`/`) — what fitcast does, the pipeline, the scoring math, sample
  results, and the engineering decisions worth talking about.
- **Live demo** (`/demo`) — paste a resume + a job URL + your own Anthropic key and
  get the real audit: verdict, the qualification arithmetic, per-requirement
  evidence, and the hybrid O*NET-ontology + LLM ATS breakdown.
- **Installable PWA** — manifest + service worker; "Add to home screen" works.

## The privacy / cost model (bring-your-own-key)

There are **no server secrets** (`.env.example` is intentionally empty).

- The browser calls `api.anthropic.com` **directly** with the visitor's key
  (`dangerouslyAllowBrowser` + the SDK's direct-browser-access header). The key
  never touches the server, so there is nothing to fund or rate-limit and no
  shared-key abuse surface. Verifiable in DevTools → Network.
- The only backend endpoint, `POST /api/fetch-posting`, exists solely to dodge
  job-board CORS. It receives **just the job URL**, is `no-store`, and is
  SSRF-hardened (DNS-resolved private/loopback/metadata ranges refused;
  redirects followed manually and re-validated).
- Claude's 20–40s analysis is **streamed from the browser**, so it never hits
  Netlify's serverless time limit.

## Fidelity to the CLI

`src/lib/` is a faithful TypeScript port of the Python scoring path so the demo
matches `python audit.py`:

| Web file | Ported from |
|---|---|
| `htmlToText.ts` | `_HTMLStripper` / `html_to_text` |
| `skills.ts` | `skill_extractor.py` (ships the exact `data/skills.json`) |
| `scoring.ts` | `derive_qualification_score` / `derive_ats_score` (incl. Python's banker's rounding) |
| `analysis.ts` | `SYSTEM_INSTRUCTIONS` + `ANALYSIS_SCHEMA` verbatim; Sonnet 4.6, adaptive thinking, JSON-schema output, cached resume prefix |
| `boards.ts` | `fetch_greenhouse/lever/ashby_jobs` + generic fallback |

## Local development

```bash
cd web
npm install
npm run dev      # http://localhost:3000
```

The demo only works over the network (job board + Anthropic) and needs a valid
`sk-ant-...` key entered in the form. Service worker registration is
production-only, so `npm run dev` won't cache stale assets.

```bash
npm run build && npm start   # production build locally
```

## Deploy to Netlify

1. Push this repo to GitHub.
2. Netlify → **Add new site → Import an existing project** → pick the repo.
3. Set **Base directory** to `web`. Build command `npm run build` and the
   `@netlify/plugin-nextjs` plugin are already declared in `web/netlify.toml`;
   Netlify auto-installs the plugin.
4. No environment variables are required. Deploy.

CLI alternative:

```bash
cd web
npm i -g netlify-cli
netlify deploy --build --prod
```

## Adding raster PWA icons (optional polish)

Modern Chromium installs fine from the bundled `icon.svg`. For maximum
cross-browser install-prompt coverage you can add `icon-192.png` /
`icon-512.png` to `public/` and reference them in
`public/manifest.webmanifest`.
