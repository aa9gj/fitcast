// Illustrative rows for the showcase "what results.csv looks like" table.
// Clearly labelled as sample data in the UI — not a live scrape.

export interface SampleRow {
  score: number;
  verdict: "qualified" | "stretch" | "not_qualified";
  ats: number;
  title: string;
  company: string;
  location: string;
  posted: string;
  missing: string[];
}

export const SAMPLE_RESULTS: SampleRow[] = [
  {
    score: 88,
    verdict: "qualified",
    ats: 81,
    title: "Senior Data Scientist, Computational Biology",
    company: "freenome",
    location: "Remote (US)",
    posted: "6h ago",
    missing: ["Spark"],
  },
  {
    score: 74,
    verdict: "stretch",
    ats: 69,
    title: "Machine Learning Engineer II",
    company: "flatironhealth",
    location: "New York, NY / Hybrid",
    posted: "20h ago",
    missing: ["Kubernetes", "MLOps platform ownership"],
  },
  {
    score: 71,
    verdict: "stretch",
    ats: 58,
    title: "Bioinformatics Scientist",
    company: "tempus",
    location: "Remote",
    posted: "1d ago",
    missing: ["nextflow", "clinical NGS pipelines"],
  },
  {
    score: 63,
    verdict: "stretch",
    ats: 77,
    title: "Quantitative Researcher",
    company: "recursionpharma",
    location: "Salt Lake City, UT",
    posted: "2d ago",
    missing: ["5+ yrs (resume ~3)", "causal inference"],
  },
  {
    score: 41,
    verdict: "not_qualified",
    ats: 44,
    title: "Staff Research Engineer, LLM Infra",
    company: "anthropic",
    location: "San Francisco, CA",
    posted: "11h ago",
    missing: ["distributed training at scale", "CUDA", "8+ yrs systems"],
  },
];
