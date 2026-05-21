// Faithful port of pipeline.py's deterministic score derivation:
// derive_qualification_score + derive_ats_score. Claude does extraction;
// these pure functions do the arithmetic — same inputs, same numbers as
// `python audit.py`.

import type {
  ATSAssessment,
  AtsScoreComponents,
  QualificationMatch,
  QualificationScoreComponents,
  Verdict,
} from "./types";
import type { SkillExtractor } from "./skills";

// Python 3 round(): round-half-to-even ("banker's rounding"). JS Math.round
// rounds half up, which diverges on exact .5 boundaries (e.g. 12.5). Match
// Python so a hand-checked score lands on the same integer.
export function pyRound(x: number, ndigits = 0): number {
  const m = 10 ** ndigits;
  const v = x * m;
  const floor = Math.floor(v);
  const diff = v - floor;
  let r: number;
  if (Math.abs(diff - 0.5) < 1e-9) {
    r = floor % 2 === 0 ? floor : floor + 1; // tie -> even
  } else {
    r = Math.round(v);
  }
  return r / m;
}

const clamp = (n: number) => Math.max(0, Math.min(100, n));

export function deriveQualificationScore(qm: QualificationMatch): {
  score: number;
  verdict: Verdict;
  components: QualificationScoreComponents;
} {
  const reqs = qm.requirements;
  const total = reqs.length;
  const met = reqs.reduce((a, r) => a + (r.met ? 1 : 0), 0);

  if (total === 0) {
    return {
      score: 50,
      verdict: "stretch",
      components: {
        requirements_met: 0,
        requirements_total: 0,
        met_ratio: null,
        base_score: 50,
        degree_penalty: 0,
        years_penalty: 0,
        adjustments_total: 0,
      },
    };
  }

  const metRatio = met / total;
  const base = metRatio * 100;

  const degreePenalty = qm.breakdown.degree_match === "below" ? -30 : 0;

  let yearsPenalty = 0;
  const yr = qm.breakdown.years_required;
  const ye = qm.breakdown.years_resume_estimated;
  if (yr !== null && ye !== null) {
    const gap = yr - ye;
    if (gap > 0) yearsPenalty = -Math.min(30, gap * 5);
  }

  const adjustments = degreePenalty + yearsPenalty;
  const score = clamp(pyRound(base + adjustments));
  const verdict: Verdict =
    score >= 80 ? "qualified" : score >= 50 ? "stretch" : "not_qualified";

  return {
    score,
    verdict,
    components: {
      requirements_met: met,
      requirements_total: total,
      met_ratio: pyRound(metRatio, 2),
      base_score: pyRound(base),
      degree_penalty: degreePenalty,
      years_penalty: yearsPenalty,
      adjustments_total: adjustments,
    },
  };
}

function intersect(a: Set<string>, b: Set<string>): Set<string> {
  const out = new Set<string>();
  for (const x of a) if (b.has(x)) out.add(x);
  return out;
}
function difference(a: Set<string>, b: Set<string>): Set<string> {
  const out = new Set<string>();
  for (const x of a) if (!b.has(x)) out.add(x);
  return out;
}
// sorted(..., key=str.lower) — stable, lowercased lexical compare.
function sortedByLower(values: string[]): string[] {
  return values
    .map((v, i) => [v, i] as const)
    .sort(([a, ai], [b, bi]) => {
      const al = a.toLowerCase();
      const bl = b.toLowerCase();
      if (al < bl) return -1;
      if (al > bl) return 1;
      return ai - bi; // preserve insertion order on ties (Python sort is stable)
    })
    .map(([v]) => v);
}

export function deriveAtsScore(
  resumeSkills: Set<string>,
  postingText: string,
  ats: ATSAssessment,
  extractor: SkillExtractor,
): {
  score: number;
  components: AtsScoreComponents;
  matched: string[];
  missing: string[];
} {
  const postingSkills = extractor.extract(postingText);
  const onetMatched = intersect(resumeSkills, postingSkills);
  const onetMissing = difference(postingSkills, resumeSkills);

  const llmMatched = new Set(ats.keyword_matches);
  const llmMissing = new Set(ats.keyword_gaps);

  const norm = (s: string) => s.trim().toLowerCase();

  const matchedLookup = new Map<string, string>();
  for (const s of onetMatched) matchedLookup.set(norm(s), s);
  for (const s of llmMatched) if (!matchedLookup.has(norm(s))) matchedLookup.set(norm(s), s);

  const missingLookup = new Map<string, string>();
  for (const s of onetMissing) missingLookup.set(norm(s), s);
  for (const s of llmMissing) {
    const n = norm(s);
    if (!missingLookup.has(n) && !matchedLookup.has(n)) missingLookup.set(n, s);
  }

  const matched = sortedByLower([...matchedLookup.values()]);
  const missing = sortedByLower([...missingLookup.values()]);
  const total = matched.length + missing.length;

  if (total === 0) {
    return {
      score: 50,
      components: {
        skills_matched: 0,
        skills_total: 0,
        match_ratio: null,
        methodology: "hybrid: O*NET ontology + Claude keyword extraction",
        onet_matched: 0,
        llm_matched: 0,
        format_warnings_count: ats.format_warnings.length,
      },
      matched,
      missing,
    };
  }

  const matchRatio = matched.length / total;
  const raw = matchRatio * 100;
  const warningPenalty = -5 * ats.format_warnings.length;
  const score = clamp(pyRound(raw + warningPenalty));

  return {
    score,
    components: {
      skills_matched: matched.length,
      skills_total: total,
      match_ratio: pyRound(matchRatio, 2),
      methodology: "hybrid: O*NET ontology + Claude keyword extraction (union)",
      onet_matched: onetMatched.size,
      onet_missing: onetMissing.size,
      llm_matched: llmMatched.size,
      llm_missing: llmMissing.size,
      format_warnings_count: ats.format_warnings.length,
      format_warnings_penalty: warningPenalty,
    },
    matched,
    missing,
  };
}
