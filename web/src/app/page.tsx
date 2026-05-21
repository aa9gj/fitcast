import Link from "next/link";
import { SAMPLE_RESULTS } from "@/lib/sample";
import { VerdictBadge, Stat } from "@/components/ui";

const REPO = "https://github.com/aa9gj/fitcast";

function PipelineStep({
  n,
  title,
  detail,
  cost,
}: {
  n: string;
  title: string;
  detail: string;
  cost: string;
}) {
  return (
    <div className="relative rounded-xl border border-ink-line bg-ink-soft p-5">
      <div className="flex items-center gap-3">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent/15 font-mono text-sm font-semibold text-accent">
          {n}
        </span>
        <h3 className="font-semibold text-zinc-100">{title}</h3>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-zinc-400">{detail}</p>
      <div className="mt-4 font-mono text-xs text-zinc-600">{cost}</div>
    </div>
  );
}

export default function Home() {
  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden border-b border-ink-line">
        <div className="grid-bg absolute inset-0" />
        <div className="relative mx-auto max-w-6xl px-5 py-20 sm:py-28">
          <div className="inline-flex items-center gap-2 rounded-full border border-ink-line bg-ink-soft px-3 py-1 font-mono text-xs text-zinc-400">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            AI engineering case study
          </div>
          <h1 className="mt-6 max-w-3xl text-4xl font-bold tracking-tight text-zinc-50 sm:text-6xl">
            Forecast your fit for jobs.
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-zinc-400">
            <span className="font-mono text-zinc-200">fitcast</span> scrapes public job
            boards, asks Claude to find the requirements buried in each posting, and
            predicts whether your resume qualifies you — with a{" "}
            <span className="text-zinc-200">transparent, auditable score</span>, not a
            black-box number.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              href="/demo"
              className="rounded-md bg-accent px-5 py-2.5 font-medium text-white hover:bg-accent-soft"
            >
              Try the live demo →
            </Link>
            <a
              href={REPO}
              target="_blank"
              rel="noreferrer"
              className="rounded-md border border-ink-line bg-ink-soft px-5 py-2.5 font-medium text-zinc-200 hover:border-zinc-600"
            >
              View source
            </a>
            <span className="font-mono text-xs text-zinc-600">
              demo runs on your own Anthropic key — nothing stored
            </span>
          </div>

          <div className="mt-14 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat k="scoring unit tests" v="146" />
            <Stat k="O*NET skills indexed" v="8.8k" />
            <Stat k="cost / full run" v="~$0.30" />
            <Stat k="LLM-picked scores" v="0" />
          </div>
        </div>
      </section>

      {/* The idea */}
      <section className="mx-auto max-w-6xl px-5 py-20">
        <div className="grid gap-12 lg:grid-cols-[1.1fr_1fr]">
          <div>
            <h2 className="text-2xl font-semibold text-zinc-100">
              The interesting problem isn&apos;t scraping — it&apos;s trust.
            </h2>
            <div className="prose-tight mt-5 space-y-4 text-zinc-400">
              <p>
                Anyone can ask an LLM &ldquo;rate my fit for this job, 0–100.&rdquo; The
                problem: the same resume and posting get 42 one run and 47 the next, and
                you can&apos;t see <em>why</em>. That&apos;s not a tool you can act on.
              </p>
              <p>
                fitcast&apos;s design decision is to give the model the job it&apos;s
                actually good at — reading a messy posting and judging each requirement
                against a resume with evidence — and move every <em>number</em> into
                deterministic Python. Claude extracts; arithmetic derives. Same inputs,
                same score, every time, with the math shown.
              </p>
              <p>
                The result is something defensible: for any job you can run{" "}
                <code className="rounded bg-ink-soft px-1.5 py-0.5 font-mono text-xs text-zinc-300">
                  audit.py
                </code>{" "}
                and read the exact evidence and arithmetic behind the verdict. The demo
                on this site reproduces that derivation in your browser.
              </p>
            </div>
          </div>
          <div className="rounded-xl border border-ink-line bg-ink-soft p-6 font-mono text-sm">
            <div className="text-zinc-500"># the score is derived, not guessed</div>
            <pre className="mt-3 overflow-x-auto whitespace-pre text-zinc-300">
{`base   = requirements_met / total * 100
        = 6 / 9 * 100         → 67

degree  meets_or_exceeds      →  0
years   5 req vs 3 on resume  → -10

score  = clamp(67 + 0 - 10)   → 57
verdict 57  (50–79)           → stretch`}
            </pre>
            <div className="mt-4 text-xs text-zinc-600">
              Every term above is emitted to results.json and printed by audit.py.
            </div>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="border-y border-ink-line bg-ink-soft/40">
        <div className="mx-auto max-w-6xl px-5 py-20">
          <h2 className="text-2xl font-semibold text-zinc-100">How a run works</h2>
          <p className="mt-3 max-w-2xl text-zinc-400">
            Token economics drive the architecture: a cheap model triages hundreds of
            candidates so the expensive model only deep-reads the few that matter.
          </p>
          <div className="mt-10 grid gap-4 md:grid-cols-3 lg:grid-cols-5">
            <PipelineStep
              n="1"
              title="Scrape"
              detail="Greenhouse, Lever, Ashby & The Muse public JSON APIs, fetched in parallel. No auth, no TOS gray area, clean data."
              cost="free"
            />
            <PipelineStep
              n="2"
              title="Pre-rank"
              detail="Haiku scores each candidate 0–10 for plausible relevance. Only the top survivors go deeper."
              cost="~$0.0007 / job"
            />
            <PipelineStep
              n="3"
              title="Deep extract"
              detail="Sonnet with adaptive thinking + JSON-schema structured output: requirements, per-item evidence, years/degree, ATS keywords."
              cost="~$0.03 / job"
            />
            <PipelineStep
              n="4"
              title="Derive"
              detail="Pure Python turns the extraction into qualification, hybrid-ATS and domain-fit scores. Deterministic & auditable."
              cost="free / local"
            />
            <PipelineStep
              n="5"
              title="Rank & write"
              detail="results.csv sorted by score with clickable apply links; results.json carries the full evidence chain."
              cost="free / local"
            />
          </div>
          <p className="mt-8 font-mono text-xs text-zinc-600">
            Pre-ranking 100 jobs with Haiku (~$0.07) then deep-analyzing only the top 10
            ≈ $0.30/run — 10× cheaper than deep-analyzing all 100, same top-of-list.
          </p>
        </div>
      </section>

      {/* Scoring */}
      <section id="scoring" className="mx-auto max-w-6xl px-5 py-20">
        <h2 className="text-2xl font-semibold text-zinc-100">Four scores, three of them deterministic</h2>
        <p className="mt-3 max-w-2xl text-zinc-400">
          Each measures something different, and they&apos;re allowed to disagree —
          that divergence is the signal.
        </p>

        <div className="mt-10 overflow-hidden rounded-xl border border-ink-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-soft text-zinc-400">
              <tr>
                <th className="px-4 py-3 font-medium">Score</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">Answers</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-line">
              {[
                ["prerank", "0–10", "Haiku (LLM)", "Is this even plausibly relevant?"],
                ["qualification", "0–100", "Derived", "Do I meet the actual requirements?"],
                ["ats", "0–100", "Derived (hybrid)", "Do my keywords match the posting's?"],
                ["domain_fit", "0–100", "Embeddings", "Is this in my field at all?"],
              ].map(([name, range, src, ans]) => (
                <tr key={name} className="bg-ink/40">
                  <td className="px-4 py-3">
                    <span className="font-mono text-zinc-200">{name}</span>{" "}
                    <span className="text-zinc-600">{range}</span>
                  </td>
                  <td className="px-4 py-3 text-zinc-400">{src}</td>
                  <td className="px-4 py-3 text-zinc-400">{ans}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-8 grid gap-5 sm:grid-cols-3">
          <div className="rounded-lg border border-verdict-qualified/30 bg-verdict-qualified/5 p-4">
            <VerdictBadge verdict="qualified" />
            <p className="mt-3 text-sm text-zinc-400">
              <span className="font-mono text-zinc-200">80–100</span> — you meet ~all
              stated requirements. Apply with the standard resume.
            </p>
          </div>
          <div className="rounded-lg border border-verdict-stretch/30 bg-verdict-stretch/5 p-4">
            <VerdictBadge verdict="stretch" />
            <p className="mt-3 text-sm text-zinc-400">
              <span className="font-mono text-zinc-200">50–79</span> — most requirements
              with gaps. Normal for any specific role; tailor and apply.
            </p>
          </div>
          <div className="rounded-lg border border-verdict-no/30 bg-verdict-no/5 p-4">
            <VerdictBadge verdict="not_qualified" />
            <p className="mt-3 text-sm text-zinc-400">
              <span className="font-mono text-zinc-200">0–49</span> — significant gaps,
              or your resume isn&apos;t surfacing the right experience.
            </p>
          </div>
        </div>

        <div className="mt-6 rounded-lg border border-ink-line bg-ink-soft p-5 text-sm text-zinc-400">
          <span className="font-medium text-zinc-200">Hybrid ATS</span> is the part
          worth stealing: a deterministic O*NET ontology pass (8.8k skills, same text →
          same skills every run) <em>unioned</em> with Claude&apos;s extraction of the
          niche, brand-new, multi-word terms an ontology can&apos;t know. Reproducible
          floor, broad ceiling. The demo runs this exact union client-side.
        </div>
      </section>

      {/* Sample results */}
      <section className="border-y border-ink-line bg-ink-soft/40">
        <div className="mx-auto max-w-6xl px-5 py-20">
          <div className="flex items-end justify-between gap-4">
            <h2 className="text-2xl font-semibold text-zinc-100">
              What <span className="font-mono">results.csv</span> looks like
            </h2>
            <span className="font-mono text-xs text-zinc-600">illustrative sample</span>
          </div>
          <div className="mt-8 overflow-x-auto rounded-xl border border-ink-line">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="bg-ink-soft text-zinc-400">
                <tr>
                  <th className="px-4 py-3 font-medium">score</th>
                  <th className="px-4 py-3 font-medium">verdict</th>
                  <th className="px-4 py-3 font-medium">ats</th>
                  <th className="px-4 py-3 font-medium">title</th>
                  <th className="px-4 py-3 font-medium">company</th>
                  <th className="px-4 py-3 font-medium">gaps</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-line">
                {SAMPLE_RESULTS.map((r) => (
                  <tr key={r.title} className="bg-ink/40">
                    <td className="px-4 py-3 font-mono tabular-nums text-zinc-200">
                      {r.score}
                    </td>
                    <td className="px-4 py-3">
                      <VerdictBadge verdict={r.verdict} />
                    </td>
                    <td className="px-4 py-3 font-mono tabular-nums text-zinc-400">
                      {r.ats}
                    </td>
                    <td className="px-4 py-3 text-zinc-300">{r.title}</td>
                    <td className="px-4 py-3 font-mono text-zinc-500">{r.company}</td>
                    <td className="px-4 py-3 text-zinc-500">
                      {r.missing.join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Built with AI — the case study */}
      <section className="mx-auto max-w-6xl px-5 py-20">
        <h2 className="text-2xl font-semibold text-zinc-100">
          What this demonstrates
        </h2>
        <p className="mt-3 max-w-2xl text-zinc-400">
          The point of the project isn&apos;t &ldquo;LLM wrapper.&rdquo; It&apos;s the
          engineering judgment around the LLM.
        </p>
        <div className="mt-10 grid gap-5 md:grid-cols-2">
          {[
            [
              "LLMs for judgment, code for numbers",
              "Claude does per-requirement extraction with evidence; Python derives every score. Reproducible and hand-checkable — the opposite of a vibe score.",
            ],
            [
              "Structured output + adaptive thinking",
              "JSON-schema constrained responses validated against a typed model, with adaptive thinking and a cached resume prefix to cut cost on repeat calls.",
            ],
            [
              "Hybrid retrieval",
              "A public-domain O*NET ontology gives a deterministic skills floor; the LLM covers the long tail. Union beats either alone — and it's testable.",
            ],
            [
              "Honest privacy architecture",
              "This web app is bring-your-own-key: the browser calls Anthropic directly, the key never touches the server. The only backend call is a keyless URL fetch.",
            ],
            [
              "Cost-aware design",
              "Cheap-model triage before expensive-model analysis takes a run from ~$3 to ~$0.30 with no quality loss in the ranked top.",
            ],
            [
              "Tested & shipped",
              "146 unit tests on the pure scoring/filter functions, CI across Python 3.10–3.12, a Colab harness, and this installable web companion.",
            ],
          ].map(([t, d]) => (
            <div
              key={t}
              className="rounded-xl border border-ink-line bg-ink-soft p-5"
            >
              <h3 className="font-semibold text-zinc-100">{t}</h3>
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">{d}</p>
            </div>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap gap-2 font-mono text-xs text-zinc-500">
          {[
            "Python",
            "Anthropic API",
            "structured outputs",
            "adaptive thinking",
            "prompt caching",
            "O*NET",
            "Next.js",
            "TypeScript",
            "Netlify",
            "PWA",
          ].map((t) => (
            <span
              key={t}
              className="rounded-md border border-ink-line bg-ink-soft px-2.5 py-1"
            >
              {t}
            </span>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-ink-line bg-gradient-to-b from-ink to-ink-soft">
        <div className="mx-auto max-w-6xl px-5 py-20 text-center">
          <h2 className="text-3xl font-semibold text-zinc-50">
            Score your own resume against a real posting
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-zinc-400">
            Paste a resume, drop in a Greenhouse / Lever / Ashby job link, bring your
            own Anthropic key. The full audit derivation runs live in your browser.
          </p>
          <Link
            href="/demo"
            className="mt-8 inline-block rounded-md bg-accent px-6 py-3 font-medium text-white hover:bg-accent-soft"
          >
            Open the live demo →
          </Link>
        </div>
      </section>
    </>
  );
}
