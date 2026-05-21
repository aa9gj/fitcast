// The ONLY server-side endpoint. It takes a public job URL and returns the
// extracted posting text. It never sees the visitor's API key or resume —
// those go straight from the browser to api.anthropic.com. Nothing here is
// logged or persisted.

import { NextResponse } from "next/server";
import { fetchPosting, PostingError } from "@/lib/boards";

export const runtime = "nodejs"; // needs node:dns / node:net for SSRF guard
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  let url: unknown;
  try {
    ({ url } = await req.json());
  } catch {
    return NextResponse.json({ error: "Expected a JSON body with a `url` field." }, { status: 400 });
  }

  if (typeof url !== "string" || url.trim().length === 0) {
    return NextResponse.json({ error: "Provide a job posting URL." }, { status: 400 });
  }
  if (url.length > 2000) {
    return NextResponse.json({ error: "That URL is implausibly long." }, { status: 400 });
  }

  try {
    const posting = await fetchPosting(url);
    return NextResponse.json(posting, {
      headers: { "cache-control": "no-store" },
    });
  } catch (e) {
    if (e instanceof PostingError) {
      return NextResponse.json({ error: e.message }, { status: 400 });
    }
    return NextResponse.json(
      { error: "Failed to fetch that posting. Try a different link." },
      { status: 502 },
    );
  }
}
