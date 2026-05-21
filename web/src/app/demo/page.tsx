"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { VerdictBadge, ScoreRing } from "@/components/ui";
import { deriveAtsScore, deriveQualificationScore } from "@/lib/scoring";
import { getExtractor } from "@/lib/skills";
import type {
  AtsScoreComponents,
  FetchedPosting,
  JobAnalysis,
  QualificationScoreComponents,
  Verdict,
} from "@/lib/types";

const EXAMPLE_RESUME = `# Alex Rivera
Data Scientist · US Citizen
alex@example.com · alexrivera.dev

## Summary
Data scientist with ~3 years of industry experience building reproducible
ML pipelines for biotech, focused on genomics and clinical data. PhD in
Computational Biology.

## Skills
- Languages: Python, R, SQL
- ML: scikit-learn, PyTorch, XGBoost, survival analysis
- Data: pandas, Airflow, Snowflake, dbt
- Bio: bulk/scRNA-seq, variant calling, GWAS

## Experience
### Colgate-Palmolive — Data Scientist (Jun 2023 – Present)
- Built reproducible Python pipelines for multi-omics biomarker discovery.
- Shipped a survival model that informed two go/no-go program decisions.
- Owned the feature store and CI for the team's models.

### Genomics Lab (PhD) — Graduate Researcher (2018 – 2023)
- Published 4 papers on statistical genetics; first-author in Nature Genetics.

## Education
PhD, Computational Biology — 2023
BS, Biology — 2018`;

type Phase = "idle" | "fetching" | "analyzing" | "deriving" | "done" | "error";

interface Derived {
  posting: FetchedPosting;
  analysis: JobAnalysis;
  qScore: number;
  qVerdict: Verdict;
  qComp: QualificationScoreComponents;
  atsScore: number;
  atsComp: AtsScoreComponents;
  atsMatched: string[];
  atsMissing: string[];
}

const KEY_LS = "fitcast.demo.apiKey";
const URL_LS = "fitcast.demo.jobUrl";

export default function DemoPage() {
  const [resume, setResume] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [remember, setRemember] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState("");
  const [result, setResult] = useState<Derived | null>(null);
  const [online, setOnline] = useState(true);
  const streamBoxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const k = localStorage.getItem(KEY_LS);
      if (k) {
        setApiKey(k);
        setRemember(true);
      }
      const u = localStorage.getItem(URL_LS);
      if (u) setJobUrl(u);
    } catch {
      /* localStorage unavailable — fine */
    }
    setOnline(navigator.onLine);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  useEffect(() => {
    if (streamBoxRef.current) {
      streamBoxRef.current.scrollTop = streamBoxRef.current.scrollHeight;
    }
  }, [stream]);

  const busy = phase === "fetching" || phase === "analyzing" || phase === "deriving";

  function persistKey(next: string, rememberNext: boolean) {
    try {
      if (rememberNext && next) localStorage.setItem(KEY_LS, next);
      else localStorage.removeItem(KEY_LS);
    } catch {
      /* ignore */
    }
  }

  async function run() {
    setError(null);
    setResult(null);
    setStream("");

    if (!resume.trim()) return setError("Paste a resume first.");
    if (!jobUrl.trim()) return setError("Paste a job posting URL.");
    if (!apiKey.trim()) return setError("Enter your Anthropic API key.");
    try {
      localStorage.setItem(URL_LS, jobUrl.trim());
    } catch {
      /* ignore */
    }

    try {
      // 1) keyless server fetch of the posting + load O*NET catalog in parallel
      setPhase("fetching");
      const [postingRes, extractor] = await Promise.all([
        fetch("/api/fetch-posting", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ url: jobUrl.trim() }),
        }),
        getExtractor(),
      ]);
      const postingJson = await postingRes.json();
      if (!postingRes.ok) {
        throw new Error(postingJson?.error || "Could not fetch that posting.");
      }
      const posting = postingJson as FetchedPosting;

      // 2) browser → Anthropic directly (key never touches our server)
      setPhase("analyzing");
      const { analyzeJob, AnalysisError } = await import("@/lib/analysis");
      let analysis: JobAnalysis;
      try {
        analysis = await analyzeJob({
          apiKey: apiKey.trim(),
          resume,
          job: {
            title: posting.title || "(title not parsed)",
            company: posting.company || "",
            location: posting.location || "",
            text: posting.text,
          },
          onThinking: (d) => setStream((s) => s + d),
          onText: (d) => setStream((s) => s + d),
        });
      } catch (e) {
        if (e instanceof AnalysisError) throw new Error(e.message);
        throw e;
      }

      // 3) deterministic derivation — identical to audit.py
      setPhase("deriving");
      const resumeSkills = extractor.extract(resume);
      const q = deriveQualificationScore(analysis.qualification_match);
      const a = deriveAtsScore(
        resumeSkills,
        posting.text,
        analysis.ats_assessment,
        extractor,
      );

      setResult({
        posting,
        analysis,
        qScore: q.score,
        qVerdict: q.verdict,
        qComp: q.components,
        atsScore: a.score,
        atsComp: a.components,
        atsMatched: a.matched,
        atsMissing: a.missing,
      });
      setPhase("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-5 py-12">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-zinc-100">Live demo</h1>
        <Link href="/" className="text-sm text-zinc-500 hover:text-zinc-300">
          ← back to overview
        </Link>
      </div>
      <p className="mt-2 max-w-2xl text-sm text-zinc-400">
        Runs the real fitcast single-job audit: server fetches the posting, your
        browser calls Claude with your key, then the qualification + hybrid-ATS
        scores are derived deterministically — the same math{" "}
        <code className="rounded bg-ink-soft px-1 py-0.5 font-mono text-xs">
          audit.py
        </code>{" "}
        prints.
      </p>

      {/* privacy panel */}
      <div className="mt-6 rounded-xl border border-accent/30 bg-accent/5 p-4 text-sm">
        <div className="font-medium text-zinc-100">Where your data goes</div>
        <ul className="mt-2 space-y-1 text-zinc-400">
          <li>
            • Your API key + resume go{" "}
            <span className="text-zinc-200">straight from this browser to
            api.anthropic.com</span>{" "}
            (open DevTools → Network to verify). They never reach this site&apos;s
            server.
          </li>
          <li>
            • The only server call is{" "}
            <code className="font-mono text-xs">/api/fetch-posting</code>, which
            receives <span className="text-zinc-200">just the job URL</span> — no key,
            no resume — and isn&apos;t logged or stored.
          </li>
          <li>
            • The key is kept in memory only, unless you tick &ldquo;remember.&rdquo;
            The resume is never persisted.
          </li>
        </ul>
      </div>

      {!online && (
        <div className="mt-4 rounded-lg border border-verdict-stretch/40 bg-verdict-stretch/10 p-3 text-sm text-verdict-stretch">
          You&apos;re offline. The demo needs the network to reach the job board and
          Anthropic — the overview pages still work offline.
        </div>
      )}

      {/* form */}
      <div className="mt-6 grid gap-5">
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="text-sm font-medium text-zinc-300">
              Resume (markdown or plain text)
            </label>
            <button
              type="button"
              onClick={() => setResume(EXAMPLE_RESUME)}
              className="font-mono text-xs text-accent hover:underline"
            >
              use example
            </button>
          </div>
          <textarea
            value={resume}
            onChange={(e) => setResume(e.target.value)}
            rows={10}
            spellCheck={false}
            placeholder="# Your Name&#10;..."
            className="w-full resize-y rounded-lg border border-ink-line bg-ink-soft p-3 font-mono text-sm text-zinc-200 outline-none focus:border-accent"
          />
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label className="text-sm font-medium text-zinc-300">
              Job posting URL
            </label>
            <input
              value={jobUrl}
              onChange={(e) => setJobUrl(e.target.value)}
              placeholder="https://boards.greenhouse.io/acme/jobs/123"
              className="mt-1.5 w-full rounded-lg border border-ink-line bg-ink-soft p-3 font-mono text-sm text-zinc-200 outline-none focus:border-accent"
            />
            <p className="mt-1.5 text-xs text-zinc-600">
              Greenhouse / Lever / Ashby links work best (clean JSON APIs).
            </p>
          </div>
          <div>
            <label className="text-sm font-medium text-zinc-300">
              Anthropic API key
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                persistKey(e.target.value, remember);
              }}
              placeholder="sk-ant-..."
              autoComplete="off"
              className="mt-1.5 w-full rounded-lg border border-ink-line bg-ink-soft p-3 font-mono text-sm text-zinc-200 outline-none focus:border-accent"
            />
            <label className="mt-1.5 flex items-center gap-2 text-xs text-zinc-500">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => {
                  setRemember(e.target.checked);
                  persistKey(apiKey, e.target.checked);
                }}
              />
              remember this key in this browser (localStorage)
            </label>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={run}
            disabled={busy}
            className="rounded-md bg-accent px-5 py-2.5 font-medium text-white hover:bg-accent-soft disabled:opacity-50"
          >
            {busy ? "Working…" : "Score this job"}
          </button>
          <span className="font-mono text-xs text-zinc-600">
            {phase === "fetching" && "fetching posting…"}
            {phase === "analyzing" && "Claude is analyzing (20–40s)…"}
            {phase === "deriving" && "deriving scores…"}
          </span>
        </div>
      </div>

      {error && (
        <div className="mt-6 rounded-lg border border-verdict-no/40 bg-verdict-no/10 p-4 text-sm text-verdict-no">
          {error}
        </div>
      )}

      {/* live stream */}
      {(phase === "analyzing" || (stream && phase !== "done")) && (
        <div className="mt-6">
          <div className="mb-1.5 flex items-center gap-2 text-xs text-zinc-500">
            <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-accent" />
            Claude is working — live output
          </div>
          <div
            ref={streamBoxRef}
            className="max-h-56 overflow-y-auto rounded-lg border border-ink-line bg-ink-soft p-3 font-mono text-xs leading-relaxed text-zinc-500 whitespace-pre-wrap"
          >
            {stream || "…"}
          </div>
        </div>
      )}

      {result && phase === "done" && <Results d={result} />}
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-6 rounded-xl border border-ink-line bg-ink-soft/50">
      <div className="border-b border-ink-line px-5 py-3 font-mono text-xs uppercase tracking-wide text-zinc-500">
        {title}
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function Num({ n }: { n: number }) {
  const s = n > 0 ? `+${n}` : `${n}`;
  return <span className="font-mono tabular-nums">{s}</span>;
}

function Results({ d }: { d: Derived }) {
  const { analysis, qComp, atsComp } = d;
  const qm = analysis.qualification_match;

  return (
    <div className="mt-10">
      <div className="rounded-xl border border-ink-line bg-ink-soft p-6">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="text-lg font-semibold text-zinc-100">
              {d.posting.title || "(title not parsed)"}
            </div>
            <div className="mt-1 font-mono text-sm text-zinc-500">
              {d.posting.company}
              {d.posting.location ? ` · ${d.posting.location}` : ""} ·{" "}
              <span className="text-zinc-600">{d.posting.source}</span>
            </div>
            <div className="mt-3">
              <VerdictBadge verdict={d.qVerdict} />
            </div>
          </div>
          <div className="flex gap-8">
            <ScoreRing value={d.qScore} label="qualification" />
            <ScoreRing value={d.atsScore} label="ATS keyword" />
          </div>
        </div>
        <p className="mt-5 border-t border-ink-line pt-4 text-sm leading-relaxed text-zinc-300">
          {qm.rationale}
        </p>
      </div>

      <Section title="Qualification score derivation (the math)">
        <div className="grid gap-2 font-mono text-sm text-zinc-400 sm:grid-cols-2">
          <div>
            requirements met:{" "}
            <span className="text-zinc-200">
              {qComp.requirements_met} / {qComp.requirements_total}
            </span>{" "}
            {qComp.met_ratio !== null && (
              <span className="text-zinc-600">(ratio {qComp.met_ratio})</span>
            )}
          </div>
          <div>
            base (ratio×100):{" "}
            <span className="text-zinc-200">{qComp.base_score}</span>
          </div>
          <div>
            degree penalty: <Num n={qComp.degree_penalty} />{" "}
            <span className="text-zinc-600">
              ({qm.breakdown.degree_resume || "—"} vs{" "}
              {qm.breakdown.degree_required || "unspecified"})
            </span>
          </div>
          <div>
            years penalty: <Num n={qComp.years_penalty} />{" "}
            <span className="text-zinc-600">
              ({qm.breakdown.years_resume_estimated ?? "—"} vs{" "}
              {qm.breakdown.years_required ?? "unspecified"} req)
            </span>
          </div>
          <div className="sm:col-span-2 mt-1 border-t border-ink-line pt-2 text-zinc-200">
            final: {qComp.base_score} <Num n={qComp.adjustments_total} /> ={" "}
            <span className="font-semibold">{d.qScore}</span> → {d.qVerdict}
          </div>
        </div>
      </Section>

      <Section title="ATS derivation (hybrid: O*NET ontology + Claude extraction)">
        <div className="grid gap-2 font-mono text-sm text-zinc-400 sm:grid-cols-2">
          <div>
            from O*NET ontology:{" "}
            <span className="text-zinc-200">{atsComp.onet_matched}</span> matched,{" "}
            {atsComp.onet_missing ?? 0} missing
          </div>
          <div>
            from Claude extraction:{" "}
            <span className="text-zinc-200">{atsComp.llm_matched}</span> matched,{" "}
            {atsComp.llm_missing ?? 0} missing
          </div>
          <div className="sm:col-span-2">
            union (dedup):{" "}
            <span className="text-zinc-200">
              {atsComp.skills_matched}/{atsComp.skills_total}
            </span>{" "}
            {atsComp.match_ratio !== null && (
              <span className="text-zinc-600">
                (ratio {atsComp.match_ratio})
              </span>
            )}
            {atsComp.format_warnings_penalty
              ? `  ·  format penalty ${atsComp.format_warnings_penalty}`
              : ""}{" "}
            → <span className="font-semibold text-zinc-200">{d.atsScore}</span>
          </div>
        </div>
      </Section>

      <Section title={`Per-requirement evidence (${qm.requirements.length})`}>
        {qm.requirements.length === 0 ? (
          <p className="text-sm text-zinc-500">No requirements identified.</p>
        ) : (
          <ul className="space-y-3">
            {qm.requirements.map((r, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span
                  className={`mt-0.5 font-mono ${
                    r.met ? "text-verdict-qualified" : "text-verdict-no"
                  }`}
                >
                  {r.met ? "✓" : "✗"}
                </span>
                <div>
                  <div className="text-zinc-200">
                    {r.requirement}{" "}
                    <span className="font-mono text-[11px] text-zinc-600">
                      [{r.confidence}]
                    </span>
                  </div>
                  <div className="mt-0.5 text-zinc-500">{r.evidence}</div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="ATS skill analysis (union of ontology + LLM extraction)">
        <div className="grid gap-6 sm:grid-cols-2">
          <div>
            <div className="text-xs text-zinc-500">
              matched ({d.atsMatched.length})
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {d.atsMatched.length === 0 && (
                <span className="text-sm text-zinc-600">—</span>
              )}
              {d.atsMatched.map((s) => (
                <span
                  key={s}
                  className="rounded bg-verdict-qualified/10 px-2 py-0.5 font-mono text-xs text-verdict-qualified"
                >
                  {s}
                </span>
              ))}
            </div>
          </div>
          <div>
            <div className="text-xs text-zinc-500">
              missing ({d.atsMissing.length})
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {d.atsMissing.length === 0 && (
                <span className="text-sm text-zinc-600">—</span>
              )}
              {d.atsMissing.map((s) => (
                <span
                  key={s}
                  className="rounded bg-verdict-no/10 px-2 py-0.5 font-mono text-xs text-verdict-no"
                >
                  {s}
                </span>
              ))}
            </div>
          </div>
        </div>
        {analysis.ats_assessment.format_warnings.length > 0 && (
          <div className="mt-4 text-xs text-verdict-stretch">
            format warnings:{" "}
            {analysis.ats_assessment.format_warnings.join(" · ")}
          </div>
        )}
      </Section>

      <details className="mt-6 rounded-xl border border-ink-line bg-ink-soft/50">
        <summary className="cursor-pointer px-5 py-3 font-mono text-xs uppercase tracking-wide text-zinc-500">
          Requirements section (verbatim from posting)
        </summary>
        <div className="border-t border-ink-line p-5">
          {analysis.requirements_section.found ? (
            <>
              {analysis.requirements_section.section_heading && (
                <div className="mb-2 text-sm font-medium text-zinc-300">
                  {analysis.requirements_section.section_heading}
                </div>
              )}
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-zinc-500">
                {analysis.requirements_section.text}
              </pre>
            </>
          ) : (
            <p className="text-sm text-zinc-500">
              No clear requirements section found in the posting.
            </p>
          )}
        </div>
      </details>

      <p className="mt-6 text-center font-mono text-xs text-zinc-600">
        same derivation as <span className="text-zinc-500">python audit.py</span> ·
        nothing on this page was stored
      </p>
    </div>
  );
}
