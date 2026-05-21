// The Claude call, ported from pipeline.py / audit.py. SYSTEM_INSTRUCTIONS and
// ANALYSIS_SCHEMA are copied verbatim so the model is asked for exactly the
// same structured extraction the CLI uses. This runs in the BROWSER with the
// visitor's own key — the key never reaches our server.

import Anthropic from "@anthropic-ai/sdk";
import type { JobAnalysis } from "./types";

// Verbatim from pipeline.py (MODEL). Sonnet 4.6 is fitcast's documented
// default; its cost figures assume it. Kept for fidelity.
export const MODEL = "claude-sonnet-4-6";

// Verbatim from pipeline.py: SYSTEM_INSTRUCTIONS.
export const SYSTEM_INSTRUCTIONS = `You are a careful job-application analyst. For each job posting, EXTRACT the following — your job is per-requirement extraction and keyword identification, not picking aggregate scores. The pipeline computes the qualification + ATS scores from your extraction.

1. Find the section that lists minimum requirements / qualifications. Common headings: "Requirements", "Minimum Qualifications", "Basic Qualifications", "What You Bring", "Qualifications", "Required Experience". Quote it verbatim in \`requirements_section.text\`. If no clear section exists, set \`found: false\`.

2. For EACH requirement you identified, output an entry in \`qualification_match.requirements\`:
   - \`requirement\`: a short paraphrase (e.g., "5+ years Python in production")
   - \`met\`: true if the resume clearly demonstrates it, false otherwise
   - \`confidence\`: "high" if the resume explicitly supports your judgment, "medium" if you're inferring (adjacent experience), "low" if it's genuinely ambiguous
   - \`evidence\`: a specific quote/reference from the resume (e.g., "Resume: 'Developed reproducible Python pipelines' at Colgate Jun 2023-Present, ~3 yrs"). For unmet requirements, write something concrete like "not mentioned in resume" or "resume shows 3 yrs vs 5+ required". This is the most important field — the user audits decisions through it.

3. Fill \`qualification_match.breakdown\` honestly with the QUANTITATIVE inputs:
   - \`years_required\`: integer years of experience the posting requires (e.g. 5 for "5+ years"). Null if not stated.
   - \`years_resume_estimated\`: integer estimate of relevant industry experience from the resume (post-degree, in roles relevant to the job). Round down.
   - \`degree_required\`: highest degree the posting requires (e.g., "PhD", "MS", "BS"). Null if not stated.
   - \`degree_resume\`: highest relevant degree on the resume.
   - \`degree_match\`: "meets_or_exceeds" if resume degree >= required (or if no degree was required), "below" if resume degree is lower, "unspecified" if posting didn't specify.

4. Provide a 2-3 sentence \`rationale\` summarizing the overall fit honestly.

5. Extract ATS keywords. The pipeline ALSO runs a deterministic O*NET ontology match independently — your job here is to catch the things the ontology might MISS: brand-new tech, niche tools, soft skills, multi-word phrases not in O*NET. Don't bother re-listing common skills (Python, SQL) — they'll be caught by the ontology. Focus on what's distinctive about THIS specific posting.
   - \`keyword_matches\`: distinctive keywords from the posting that DO appear in the resume (only include things that aren't trivially obvious — focus on non-O*NET terms)
   - \`keyword_gaps\`: distinctive keywords from the posting that are MISSING from the resume
   - \`format_warnings\`: any format issues you can detect from the resume markdown (almost always empty for markdown input — empty list is fine)

Be terse. Ground every claim in what's actually in the resume. The Python pipeline derives the scores from this extraction — do NOT pick scores yourself.`;

// Verbatim from pipeline.py: ANALYSIS_SCHEMA.
export const ANALYSIS_SCHEMA = {
  type: "object",
  properties: {
    requirements_section: {
      type: "object",
      properties: {
        found: { type: "boolean" },
        section_heading: { type: ["string", "null"] },
        text: {
          type: "string",
          description: "Verbatim quote of the requirements section.",
        },
      },
      required: ["found", "section_heading", "text"],
      additionalProperties: false,
    },
    qualification_match: {
      type: "object",
      properties: {
        requirements: {
          type: "array",
          description:
            "One entry per requirement found in the posting's requirements section.",
          items: {
            type: "object",
            properties: {
              requirement: { type: "string" },
              met: { type: "boolean" },
              confidence: { type: "string", enum: ["high", "medium", "low"] },
              evidence: { type: "string" },
            },
            required: ["requirement", "met", "confidence", "evidence"],
            additionalProperties: false,
          },
        },
        breakdown: {
          type: "object",
          properties: {
            years_required: { type: ["integer", "null"] },
            years_resume_estimated: { type: ["integer", "null"] },
            degree_required: { type: ["string", "null"] },
            degree_resume: { type: ["string", "null"] },
            degree_match: {
              type: "string",
              enum: ["meets_or_exceeds", "below", "unspecified"],
            },
          },
          required: [
            "years_required",
            "years_resume_estimated",
            "degree_required",
            "degree_resume",
            "degree_match",
          ],
          additionalProperties: false,
        },
        rationale: { type: "string" },
      },
      required: ["requirements", "breakdown", "rationale"],
      additionalProperties: false,
    },
    ats_assessment: {
      type: "object",
      properties: {
        keyword_matches: {
          type: "array",
          items: { type: "string" },
          description:
            "Important keywords from the posting that DO appear in the resume.",
        },
        keyword_gaps: {
          type: "array",
          items: { type: "string" },
          description:
            "Important keywords from the posting that are MISSING from the resume.",
        },
        format_warnings: {
          type: "array",
          items: { type: "string" },
          description:
            "Resume format issues detectable from text alone (rare with markdown).",
        },
      },
      required: ["keyword_matches", "keyword_gaps", "format_warnings"],
      additionalProperties: false,
    },
  },
  required: ["requirements_section", "qualification_match", "ats_assessment"],
  additionalProperties: false,
} as const;

export interface AnalyzeArgs {
  apiKey: string;
  resume: string;
  job: { title: string; company: string; location: string; text: string };
  onThinking?: (delta: string) => void;
  onText?: (delta: string) => void;
}

export class AnalysisError extends Error {
  constructor(
    message: string,
    readonly kind: "auth" | "rate_limit" | "api" | "parse" | "empty",
  ) {
    super(message);
  }
}

export async function analyzeJob({
  apiKey,
  resume,
  job,
  onThinking,
  onText,
}: AnalyzeArgs): Promise<JobAnalysis> {
  const client = new Anthropic({
    apiKey,
    // The key is supplied by the visitor at runtime and never bundled or
    // persisted server-side; the SDK adds the direct-browser-access header
    // and the request goes straight from the browser to api.anthropic.com.
    dangerouslyAllowBrowser: true,
  });

  const userMessage =
    `## Job: ${job.title} at ${job.company}\n` +
    `Location: ${job.location}\n\n` +
    `## Posting:\n${job.text}`;

  // Mirrors audit.py: cached resume system block, adaptive thinking,
  // json_schema structured output at high effort. `output_config` /
  // adaptive `thinking` are current API features; cast tolerates SDK
  // type-version drift without losing the real request shape.
  const params = {
    model: MODEL,
    max_tokens: 8000,
    system: [
      { type: "text", text: SYSTEM_INSTRUCTIONS },
      {
        type: "text",
        text: `## Candidate Resume\n\n${resume}`,
        cache_control: { type: "ephemeral" },
      },
    ],
    thinking: { type: "adaptive" },
    output_config: {
      effort: "high",
      format: { type: "json_schema", schema: ANALYSIS_SCHEMA },
    },
    messages: [{ role: "user", content: userMessage }],
  };

  let stream;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    stream = client.messages.stream(params as any);
  } catch (e) {
    throw toAnalysisError(e);
  }

  try {
    for await (const event of stream) {
      if (event.type === "content_block_delta") {
        const d = event.delta as { type: string; text?: string; thinking?: string };
        if (d.type === "text_delta" && d.text) onText?.(d.text);
        else if (d.type === "thinking_delta" && d.thinking) onThinking?.(d.thinking);
      }
    }
    const final = await stream.finalMessage();
    const textBlock = final.content.find((b) => b.type === "text") as
      | { type: "text"; text: string }
      | undefined;
    if (!textBlock || !textBlock.text) {
      throw new AnalysisError("Claude returned an empty response.", "empty");
    }
    try {
      return JSON.parse(textBlock.text) as JobAnalysis;
    } catch {
      throw new AnalysisError("Could not parse Claude's analysis as JSON.", "parse");
    }
  } catch (e) {
    if (e instanceof AnalysisError) throw e;
    throw toAnalysisError(e);
  }
}

function toAnalysisError(e: unknown): AnalysisError {
  if (e instanceof Anthropic.AuthenticationError) {
    return new AnalysisError(
      "That API key was rejected by Anthropic (401). Check the key and try again.",
      "auth",
    );
  }
  if (e instanceof Anthropic.RateLimitError) {
    return new AnalysisError(
      "Anthropic rate-limited this key (429). Wait a moment and retry.",
      "rate_limit",
    );
  }
  if (e instanceof Anthropic.APIError) {
    return new AnalysisError(`Anthropic API error: ${e.message}`, "api");
  }
  const msg = e instanceof Error ? e.message : String(e);
  return new AnalysisError(msg || "Unknown error calling Anthropic.", "api");
}
