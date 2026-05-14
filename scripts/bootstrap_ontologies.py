#!/usr/bin/env python3
"""bootstrap_ontologies.py — download O*NET and build a normalized skill list.

Downloads the O*NET database (free, public domain — published by US Department
of Labor), extracts the Technology Skills + Knowledge areas, normalizes the
names, and writes data/skills.json. The skill_extractor module reads this
file at import time.

Re-run periodically (twice a year) when O*NET publishes a new version.

Usage:
    python bootstrap_ontologies.py           # download + build with default version
    python bootstrap_ontologies.py --force   # re-download even if cached
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# `requests` is the preferred downloader (uses certifi for SSL) but not strictly
# required — falls back to curl. Avoids stdlib urllib's macOS-Framework SSL
# certificate-bundle issues.
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# This script lives in scripts/ but writes to the project's data/ directory,
# so ROOT must walk up one level.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SKILLS_PATH = DATA_DIR / "skills.json"
SUPPLEMENT_PATH = DATA_DIR / "skills_supplement.txt"
ONET_CACHE_PATH = DATA_DIR / ".onet_db.zip"

# O*NET 28.3 (current as of mid-2026). Update this URL when newer versions ship.
ONET_VERSION = "28.3"
ONET_URL = f"https://www.onetcenter.org/dl_files/database/db_{ONET_VERSION.replace('.', '_')}_text.zip"


def download_onet(force: bool = False) -> Path:
    """Download the O*NET text database zip. Caches in data/.onet_db.zip."""
    DATA_DIR.mkdir(exist_ok=True)
    if ONET_CACHE_PATH.exists() and not force:
        size_mb = ONET_CACHE_PATH.stat().st_size / 1024 / 1024
        print(f"Using cached O*NET database ({size_mb:.1f} MB at {ONET_CACHE_PATH.name}).",
              file=sys.stderr)
        return ONET_CACHE_PATH

    print(f"Downloading O*NET {ONET_VERSION} database (~13MB)...", file=sys.stderr)
    print(f"  {ONET_URL}", file=sys.stderr)
    if _HAS_REQUESTS:
        resp = requests.get(ONET_URL, timeout=120, stream=True)
        resp.raise_for_status()
        ONET_CACHE_PATH.write_bytes(resp.content)
    else:
        if not shutil.which("curl"):
            sys.exit("Error: neither `requests` (Python) nor `curl` is available. "
                     "Install requests with `pip install requests` or install curl.")
        result = subprocess.run(
            ["curl", "-sSfL", "-o", str(ONET_CACHE_PATH), ONET_URL],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f"Error downloading O*NET via curl: {result.stderr}")
    data = ONET_CACHE_PATH.read_bytes()
    print(f"  -> cached at {ONET_CACHE_PATH.relative_to(ROOT)} ({len(data)/1024/1024:.1f} MB)",
          file=sys.stderr)
    return ONET_CACHE_PATH


def _read_zip_member(zip_path: Path, member_pattern: str) -> str:
    """Read the first file in zip_path whose name matches member_pattern (regex)."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if re.search(member_pattern, name, re.IGNORECASE):
                with zf.open(name) as f:
                    return f.read().decode("utf-8", errors="replace")
    raise FileNotFoundError(f"No member matching {member_pattern!r} in {zip_path}")


def parse_tab_file(text: str) -> list[dict]:
    """Parse an O*NET tab-separated file into a list of dicts."""
    lines = text.strip().split("\n")
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def normalize(name: str) -> str:
    """Normalize a skill name for matching. Lowercase, strip parentheticals,
    collapse whitespace, remove trailing punctuation."""
    if not name:
        return ""
    s = name.strip()
    # Drop parentheticals: "Python (programming language)" -> "Python"
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    # Collapse whitespace, lowercase
    s = re.sub(r"\s+", " ", s).strip().lower()
    # Strip surrounding punctuation
    s = s.strip(".,;:")
    return s


def is_useful_skill(name: str) -> bool:
    """Filter out skill entries that are unlikely to appear in tech/business job postings."""
    n = name.strip()
    if len(n) < 2 or n.isdigit():
        return False
    # Generic single words that defeat the keyword-filter purpose.
    if n.lower() in {"data", "analysis", "software", "system", "computer", "internet"}:
        return False
    # Filter out obvious physical tools (these come from O*NET's "Tools Used" file
    # and are dominated by manual labor / trade equipment that won't appear in
    # software / business / scientific job postings).
    physical_indicators = {
        "hook", "lift", "shovel", "rake", "saw", "drill", "wrench", "hammer",
        "axe", "broom", "buffer", "buret", "centrifuge", "scalpel", "forceps",
        "trowel", "harvester", "mower", "tractor", "plow", "tiller", "auger",
        "blade", "knife", "gloves", "boots", "ladder", "scaffold", "harness",
        "bucket", "pan", "pot", "skewer", "spatula", "tongs", "whisk",
        "feeder", "trough", "stable", "fence", "trap", "decoy", "lure",
        "anchor", "buoy", "net", "sail", "rudder", "mast", "winch",
    }
    name_lower = n.lower()
    if any(word in name_lower.split() for word in physical_indicators):
        return False
    return True


def load_supplement() -> list[str]:
    """Load the manually-curated skill supplement (modern tech terms O*NET misses)."""
    if not SUPPLEMENT_PATH.exists():
        return []
    skills = []
    for line in SUPPLEMENT_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        skills.append(line)
    return skills


def extract_skills_from_onet(zip_path: Path, include_tools: bool = False) -> dict:
    """Extract a deduped skill list from the O*NET database zip."""
    print("Parsing O*NET database files...", file=sys.stderr)

    # Technology Skills: software / web tools / programming languages — the
    # most relevant file for tech / business / scientific job postings.
    tech_text = _read_zip_member(zip_path, r"Technology Skills\.txt$")
    tech_rows = parse_tab_file(tech_text)
    print(f"  Technology Skills:  {len(tech_rows)} rows", file=sys.stderr)

    # Knowledge: broad domain areas ("Biology", "Mathematics", "Chemistry").
    knowledge_names: set[str] = set()
    try:
        knowledge_text = _read_zip_member(zip_path, r"Knowledge\.txt$")
        knowledge_rows = parse_tab_file(knowledge_text)
        knowledge_names = {r.get("Element Name", "").strip() for r in knowledge_rows}
        knowledge_names.discard("")
        print(f"  Knowledge areas:    {len(knowledge_names)} unique", file=sys.stderr)
    except FileNotFoundError:
        pass

    # Tools Used: physical equipment (microscopes, sequencers, but also lots of
    # construction / agriculture / kitchen equipment). Off by default — even
    # filtered, it adds more noise than signal for typical fitcast users.
    tools_rows: list[dict] = []
    if include_tools:
        try:
            tools_text = _read_zip_member(zip_path, r"Tools Used\.txt$")
            tools_rows = parse_tab_file(tools_text)
            print(f"  Tools Used:         {len(tools_rows)} rows", file=sys.stderr)
        except FileNotFoundError:
            pass

    raw_skills: dict[str, str] = {}  # normalized -> canonical

    for row in tech_rows:
        name = row.get("Example", "").strip()
        if not is_useful_skill(name):
            continue
        norm = normalize(name)
        if norm not in raw_skills or any(c.isupper() for c in name):
            raw_skills[norm] = name

    for row in tools_rows:
        name = row.get("Example", "").strip()
        if not is_useful_skill(name):
            continue
        norm = normalize(name)
        if norm not in raw_skills:
            raw_skills[norm] = name

    for name in knowledge_names:
        if not is_useful_skill(name):
            continue
        norm = normalize(name)
        if norm not in raw_skills:
            raw_skills[norm] = name

    onet_count = len(raw_skills)
    print(f"  O*NET → {onet_count} skills after filtering", file=sys.stderr)

    # Merge in the manually-curated supplement.
    supplement = load_supplement()
    supplement_added = 0
    for name in supplement:
        norm = normalize(name)
        if norm not in raw_skills:
            raw_skills[norm] = name
            supplement_added += 1
    if supplement:
        print(f"  Supplement → {supplement_added} new skills (from {SUPPLEMENT_PATH.name})",
              file=sys.stderr)

    skills_sorted = sorted(raw_skills.values(), key=lambda s: s.lower())

    return {
        "version": f"O*NET {ONET_VERSION}",
        "source_url": ONET_URL,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "license": "O*NET data is public domain — published by US Department of Labor.",
        "include_tools": include_tools,
        "supplement_count": supplement_added,
        "onet_count": onet_count,
        "skill_count": len(skills_sorted),
        "skills": skills_sorted,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--force", action="store_true",
                    help="Re-download O*NET zip even if cached.")
    ap.add_argument("--include-tools", action="store_true",
                    help="Also include O*NET's 'Tools Used' data (mostly physical "
                         "equipment — adds noise for tech/business roles, but "
                         "includes some lab instruments for biotech).")
    args = ap.parse_args()

    zip_path = download_onet(force=args.force)
    skills_data = extract_skills_from_onet(zip_path, include_tools=args.include_tools)

    DATA_DIR.mkdir(exist_ok=True)
    SKILLS_PATH.write_text(json.dumps(skills_data, indent=2))

    print(f"\n✓ Wrote {SKILLS_PATH.relative_to(ROOT)}", file=sys.stderr)
    print(f"  {skills_data['skill_count']} unique skills from {skills_data['version']}",
          file=sys.stderr)
    print(f"  License: {skills_data['license']}", file=sys.stderr)


if __name__ == "__main__":
    main()
