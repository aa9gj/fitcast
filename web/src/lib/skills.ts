// Faithful port of skill_extractor.py — the deterministic O*NET n-gram matcher.
// The catalog (public/skills.json) is the exact file the Python pipeline ships
// (~8,800 skills: O*NET 28.3 + curated supplement). Same text -> same skills,
// so the web demo's ATS "ontology" half matches the CLI byte-for-byte.
//
// Loaded lazily in the browser so the 288KB catalog stays out of the main
// bundle and only downloads when someone actually runs the demo.

function normalize(s: string): string {
  if (!s) return "";
  s = s.replace(/\s*\([^)]*\)\s*/g, " ");
  s = s.replace(/\s+/g, " ").trim().toLowerCase();
  s = s.replace(/^[.,;:]+|[.,;:]+$/g, "");
  return s;
}

function tokenize(text: string): string[] {
  text = text.toLowerCase();
  // Python uses \w (Unicode). Keep letters/numbers/underscore plus the
  // intra-word punctuation that shows up in tech terms (C++, .NET, Node.js).
  text = text.replace(/[^\p{L}\p{N}_\s.+/#-]/gu, " ");
  return text.split(/\s+/).filter(Boolean);
}

function upperCount(s: string): number {
  const m = s.match(/\p{Lu}/gu);
  return m ? m.length : 0;
}

export class SkillExtractor {
  private lookup = new Map<string, string>();
  private maxNgram = 1;

  constructor(canonicalSkills: string[]) {
    for (const s of canonicalSkills) {
      const norm = normalize(s);
      if (!norm) continue;
      const existing = this.lookup.get(norm);
      if (existing === undefined || upperCount(s) > upperCount(existing)) {
        this.lookup.set(norm, s);
      }
    }
    let max = 1;
    for (const key of this.lookup.keys()) {
      const len = tokenize(key).length;
      if (len > max) max = len;
    }
    // Real O*NET phrases top out well under this; cap defensively so a freak
    // catalog entry can't turn extract() into an O(n * huge) scan.
    this.maxNgram = Math.min(max, 12);
  }

  extract(text: string): Set<string> {
    const found = new Set<string>();
    if (!text) return found;
    const words = tokenize(text);
    const nMax = this.maxNgram;
    for (let i = 0; i < words.length; i++) {
      const upper = Math.min(nMax, words.length - i);
      for (let n = 1; n <= upper; n++) {
        const ngram = normalize(words.slice(i, i + n).join(" "));
        const hit = this.lookup.get(ngram);
        if (hit !== undefined) found.add(hit);
      }
    }
    return found;
  }
}

let extractorPromise: Promise<SkillExtractor> | null = null;

export function getExtractor(): Promise<SkillExtractor> {
  if (!extractorPromise) {
    extractorPromise = fetch("/skills.json")
      .then((r) => {
        if (!r.ok) throw new Error(`skills.json ${r.status}`);
        return r.json();
      })
      .then((data: { skills: string[] }) => new SkillExtractor(data.skills))
      .catch((e) => {
        extractorPromise = null; // allow retry on next demo run
        throw e;
      });
  }
  return extractorPromise;
}
