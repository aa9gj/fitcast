// Faithful port of pipeline.py's _HTMLStripper + html_to_text.
//
// Block tags become newlines, <li> becomes "\n- ", headings get blank lines,
// then runs of blank lines / spaces are collapsed. Python's HTMLParser runs
// with convert_charrefs=True, so entities are decoded before text is emitted —
// we replicate that here.

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  "#39": "'",
  nbsp: " ",
  mdash: "—",
  ndash: "–",
  hellip: "…",
  rsquo: "’",
  lsquo: "‘",
  ldquo: "“",
  rdquo: "”",
  middot: "·",
  bull: "•",
  trade: "™",
  reg: "®",
  copy: "©",
  deg: "°",
  eacute: "é",
  egrave: "è",
  agrave: "à",
  uuml: "ü",
  ouml: "ö",
  auml: "ä",
};

function decodeEntities(s: string): string {
  return s.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);/g, (whole, body: string) => {
    if (body[0] === "#") {
      const isHex = body[1] === "x" || body[1] === "X";
      const code = parseInt(isHex ? body.slice(2) : body.slice(1), isHex ? 16 : 10);
      if (Number.isFinite(code) && code > 0) {
        try {
          return String.fromCodePoint(code);
        } catch {
          return whole;
        }
      }
      return whole;
    }
    const named = NAMED_ENTITIES[body] ?? NAMED_ENTITIES[body.toLowerCase()];
    return named ?? whole;
  });
}

export function htmlToText(html: string): string {
  if (!html) return "";

  // Greenhouse returns `content` as fully entity-escaped markup (&lt;div&gt;…)
  // with no literal tags. Decode once up front so the structural pass below
  // can actually see the tags. Real HTML (Lever/Ashby/generic) has literal
  // `<tags>` and is left untouched — keeping that path a 1:1 port of
  // pipeline.py's _HTMLStripper.
  if (!/<[a-zA-Z!/]/.test(html) && /&lt;/.test(html)) {
    html = decodeEntities(html);
  }

  const parts: string[] = [];
  // Tokenize into tags / comments / text. Mirrors HTMLParser's tag dispatch.
  const tokenRe = /<!--[\s\S]*?-->|<[^>]+>|[^<]+/g;
  const matches = html.match(tokenRe);
  if (!matches) return "";

  for (const tok of matches) {
    if (tok.startsWith("<!--")) {
      continue; // HTMLParser routes comments away from handle_data
    }
    if (tok[0] === "<") {
      if (tok[1] === "!" || tok[1] === "?") continue; // doctype / PI
      const isEnd = tok[1] === "/";
      const nameMatch = tok.slice(isEnd ? 2 : 1).match(/^[a-zA-Z][a-zA-Z0-9]*/);
      if (!nameMatch) continue;
      const tag = nameMatch[0].toLowerCase();
      if (!isEnd) {
        if (tag === "p" || tag === "br" || tag === "div") parts.push("\n");
        else if (tag === "li") parts.push("\n- ");
        else if (tag === "h1" || tag === "h2" || tag === "h3" || tag === "h4")
          parts.push("\n\n");
      } else {
        if (
          tag === "p" ||
          tag === "div" ||
          tag === "h1" ||
          tag === "h2" ||
          tag === "h3" ||
          tag === "h4"
        )
          parts.push("\n");
      }
    } else {
      parts.push(decodeEntities(tok));
    }
  }

  let text = parts.join("");
  text = text.replace(/\n{3,}/g, "\n\n");
  text = text.replace(/[ \t]+/g, " ");
  return text.trim();
}
