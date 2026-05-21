// Server-side posting fetcher. The browser can't fetch job boards directly
// (CORS), so this tiny endpoint helper does it. It holds NO secrets — it only
// ever sees a public job URL. Mirrors pipeline.py's source fetchers
// (Greenhouse / Lever / Ashby public JSON APIs) with a best-effort generic
// fallback for everything else (e.g. The Muse landing pages).

import { lookup } from "node:dns/promises";
import { isIP } from "node:net";
import { htmlToText } from "./htmlToText";
import type { FetchedPosting } from "./types";

const HTTP_TIMEOUT_MS = 20_000; // matches pipeline.py HTTP_TIMEOUT_S = 20
const UA =
  "Mozilla/5.0 (compatible; fitcast-demo/1.0; +https://github.com/aa9gj/fitcast)";

export class PostingError extends Error {}

// ---- SSRF hardening ---------------------------------------------------------
// /api/fetch-posting takes a user-supplied URL and fetches it server-side, so
// it must refuse internal targets (loopback, RFC1918, link-local, cloud
// metadata). We resolve DNS and check every answer; redirects on the generic
// path are followed manually and re-validated each hop.

function ipIsPrivate(ip: string): boolean {
  if (isIP(ip) === 6) {
    const v = ip.toLowerCase();
    return (
      v === "::1" ||
      v === "::" ||
      v.startsWith("fe80") || // link-local
      v.startsWith("fc") ||
      v.startsWith("fd") || // unique local
      v.startsWith("::ffff:") // IPv4-mapped — defer to v4 check below
    );
  }
  const p = ip.split(".").map(Number);
  if (p.length !== 4 || p.some((n) => Number.isNaN(n))) return true;
  const [a, b] = p;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 169 && b === 254) || // link-local incl. 169.254.169.254 metadata
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224 // multicast / reserved
  );
}

async function assertPublicUrl(raw: string): Promise<URL> {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    throw new PostingError("That doesn't look like a valid URL.");
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    throw new PostingError("Only http(s) URLs are supported.");
  }
  const host = u.hostname;
  if (
    !host ||
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host.endsWith(".internal") ||
    host.endsWith(".local")
  ) {
    throw new PostingError("Refusing to fetch an internal hostname.");
  }
  if (isIP(host)) {
    if (ipIsPrivate(host)) throw new PostingError("Refusing to fetch a private address.");
    return u;
  }
  let addrs;
  try {
    addrs = await lookup(host, { all: true });
  } catch {
    throw new PostingError(`Could not resolve ${host}.`);
  }
  if (addrs.length === 0 || addrs.some((a) => ipIsPrivate(a.address))) {
    throw new PostingError("That hostname resolves to a non-public address.");
  }
  return u;
}

async function timedFetch(url: string, init?: RequestInit): Promise<Response> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...init,
      signal: ctrl.signal,
      headers: { "user-agent": UA, accept: "*/*", ...(init?.headers || {}) },
    });
  } finally {
    clearTimeout(t);
  }
}

// Trusted, fixed board API hosts — safe to let fetch follow redirects.
async function getJson(url: string): Promise<any | null> {
  try {
    const r = await timedFetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

// ---- URL routing ------------------------------------------------------------

type Route =
  | { kind: "greenhouse"; slug: string; id: string }
  | { kind: "lever"; slug: string; id: string }
  | { kind: "ashby"; slug: string; id: string }
  | { kind: "generic" };

function routeFor(u: URL): Route {
  const host = u.hostname.toLowerCase();
  const segs = u.pathname.split("/").filter(Boolean);

  if (host.endsWith("greenhouse.io")) {
    if (segs[0] === "embed" && segs[1] === "job_app") {
      const slug = u.searchParams.get("for");
      const id = u.searchParams.get("token");
      if (slug && id) return { kind: "greenhouse", slug, id };
    }
    // /{slug}/jobs/{id}
    const ji = segs.indexOf("jobs");
    if (ji >= 1 && segs[ji + 1]) {
      return { kind: "greenhouse", slug: segs[ji - 1], id: segs[ji + 1] };
    }
  }

  if (host.endsWith("lever.co")) {
    // /{slug}/{id}[/apply]
    if (segs.length >= 2) return { kind: "lever", slug: segs[0], id: segs[1] };
  }

  if (host.endsWith("ashbyhq.com")) {
    // /{slug}/{uuid}[/application]
    if (segs.length >= 2) return { kind: "ashby", slug: segs[0], id: segs[1] };
  }

  return { kind: "generic" };
}

// ---- Per-source extraction --------------------------------------------------

async function fromGreenhouse(slug: string, id: string, url: string): Promise<FetchedPosting> {
  const j = await getJson(
    `https://boards-api.greenhouse.io/v1/boards/${encodeURIComponent(
      slug,
    )}/jobs/${encodeURIComponent(id)}?questions=false`,
  );
  if (!j || !j.content) {
    throw new PostingError(
      "Greenhouse didn't return this posting (it may be closed or the link is an embed we can't resolve).",
    );
  }
  return {
    source: "greenhouse",
    title: j.title || "",
    company: j.company_name || slug,
    location: j.location?.name || "",
    text: htmlToText(j.content),
    url: j.absolute_url || url,
  };
}

async function fromLever(slug: string, id: string, url: string): Promise<FetchedPosting> {
  const j = await getJson(
    `https://api.lever.co/v0/postings/${encodeURIComponent(slug)}/${encodeURIComponent(
      id,
    )}?mode=json`,
  );
  if (!j) {
    throw new PostingError("Lever didn't return this posting (it may be closed).");
  }
  const lists: string = Array.isArray(j.lists)
    ? j.lists.map((l: any) => `<h3>${l.text || ""}</h3>${l.content || ""}`).join("")
    : "";
  const html = `${j.description || ""}${lists}${j.additional || ""}`;
  return {
    source: "lever",
    title: j.text || "",
    company: slug,
    location: j.categories?.location || "",
    text: htmlToText(html),
    url: j.hostedUrl || url,
  };
}

async function fromAshby(slug: string, id: string, url: string): Promise<FetchedPosting> {
  const j = await getJson(
    `https://api.ashbyhq.com/posting-api/job-board/${encodeURIComponent(slug)}`,
  );
  const jobs: any[] = j?.jobs || [];
  const match =
    jobs.find((x) => String(x.id) === id) ||
    jobs.find((x) => typeof x.jobUrl === "string" && x.jobUrl.includes(id));
  if (!match || !match.descriptionHtml) {
    throw new PostingError(
      "Ashby didn't return this posting (it may be closed or filled).",
    );
  }
  return {
    source: "ashby",
    title: match.title || "",
    company: j?.organizationName || slug,
    location: match.location || "",
    text: htmlToText(match.descriptionHtml),
    url: match.jobUrl || url,
  };
}

async function fromGeneric(start: URL): Promise<FetchedPosting> {
  // Follow redirects by hand, re-validating each hop (closes the
  // "public URL 302s to 169.254.169.254" SSRF).
  let current = start;
  let html = "";
  for (let hop = 0; hop < 4; hop++) {
    const r = await timedFetch(current.toString(), { redirect: "manual" });
    if (r.status >= 300 && r.status < 400 && r.headers.get("location")) {
      current = await assertPublicUrl(new URL(r.headers.get("location")!, current).toString());
      continue;
    }
    if (!r.ok) {
      throw new PostingError(`The page returned HTTP ${r.status}.`);
    }
    html = await r.text();
    break;
  }
  // Drop non-content blocks before stripping (board APIs are clean, arbitrary
  // pages are not). htmlToText itself stays a faithful 1:1 port.
  const cleaned = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
    .replace(/<svg[\s\S]*?<\/svg>/gi, " ")
    .replace(/<head[\s\S]*?<\/head>/gi, " ");
  const text = htmlToText(cleaned);
  if (text.length < 200) {
    throw new PostingError(
      "Couldn't extract a readable posting from that URL — it's likely JavaScript-rendered. " +
        "Try a direct Greenhouse, Lever, or Ashby job link.",
    );
  }
  return {
    source: "generic",
    title: "",
    company: start.hostname.replace(/^www\./, ""),
    location: "",
    text,
    url: start.toString(),
  };
}

export async function fetchPosting(rawUrl: string): Promise<FetchedPosting> {
  const u = await assertPublicUrl(rawUrl.trim());
  const route = routeFor(u);
  switch (route.kind) {
    case "greenhouse":
      return fromGreenhouse(route.slug, route.id, u.toString());
    case "lever":
      return fromLever(route.slug, route.id, u.toString());
    case "ashby":
      return fromAshby(route.slug, route.id, u.toString());
    default:
      return fromGeneric(u);
  }
}
