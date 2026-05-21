// Shapes mirror the Pydantic models + schema in the fitcast pipeline
// (pipeline.py: RequirementEvidence / RequirementBreakdown / RequirementsSection
// / QualificationMatch / ATSAssessment / JobAnalysis). Kept 1:1 so the web
// demo's scoring is identical to `python audit.py`.

export type Confidence = "high" | "medium" | "low";
export type DegreeMatch = "meets_or_exceeds" | "below" | "unspecified";

export interface RequirementEvidence {
  requirement: string;
  met: boolean;
  confidence: Confidence;
  evidence: string;
}

export interface RequirementBreakdown {
  years_required: number | null;
  years_resume_estimated: number | null;
  degree_required: string | null;
  degree_resume: string | null;
  degree_match: DegreeMatch;
}

export interface RequirementsSection {
  found: boolean;
  section_heading: string | null;
  text: string;
}

export interface QualificationMatch {
  requirements: RequirementEvidence[];
  breakdown: RequirementBreakdown;
  rationale: string;
}

export interface ATSAssessment {
  keyword_matches: string[];
  keyword_gaps: string[];
  format_warnings: string[];
}

export interface JobAnalysis {
  requirements_section: RequirementsSection;
  qualification_match: QualificationMatch;
  ats_assessment: ATSAssessment;
}

export type Verdict = "qualified" | "stretch" | "not_qualified";

export interface QualificationScoreComponents {
  requirements_met: number;
  requirements_total: number;
  met_ratio: number | null;
  base_score: number;
  degree_penalty: number;
  years_penalty: number;
  adjustments_total: number;
}

export interface AtsScoreComponents {
  skills_matched: number;
  skills_total: number;
  match_ratio: number | null;
  methodology: string;
  onet_matched: number;
  onet_missing?: number;
  llm_matched: number;
  llm_missing?: number;
  format_warnings_count: number;
  format_warnings_penalty?: number;
}

// What /api/fetch-posting returns to the browser.
export interface FetchedPosting {
  source: "greenhouse" | "lever" | "ashby" | "generic";
  title: string;
  company: string;
  location: string;
  text: string;
  url: string;
}
