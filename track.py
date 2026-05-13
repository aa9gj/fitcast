#!/usr/bin/env python3
"""track.py — record which jobs you've applied to.

State lives in applied.json. pipeline.py reads this file and skips any job
whose URL appears here, so you don't re-analyze jobs you've already acted on.

Usage:
    python track.py mark <url> [status]     # default status: applied
    python track.py list                    # show everything, sorted by date
    python track.py list <status>           # filter by status
    python track.py remove <url>            # untrack a job

Conventional statuses (free-form — use whatever you like):
    interested, applied, phone_screen, interview, offer, rejected, withdrew
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
APPLIED_PATH = ROOT / "applied.json"


def load() -> dict:
    if not APPLIED_PATH.exists():
        return {}
    try:
        return json.loads(APPLIED_PATH.read_text())
    except json.JSONDecodeError:
        sys.exit(f"Error: {APPLIED_PATH} exists but isn't valid JSON.")


def save(data: dict) -> None:
    APPLIED_PATH.write_text(json.dumps(data, indent=2, sort_keys=True))


def cmd_mark(args: argparse.Namespace) -> None:
    data = load()
    entry = data.get(args.url, {})
    entry["status"] = args.status
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    if args.status == "applied" and not entry.get("applied_at"):
        entry["applied_at"] = entry["updated_at"]
    if args.title:
        entry["title"] = args.title
    if args.company:
        entry["company"] = args.company
    if args.notes:
        entry["notes"] = args.notes
    data[args.url] = entry
    save(data)
    print(f"Marked {args.url} -> {args.status}")


def cmd_list(args: argparse.Namespace) -> None:
    data = load()
    if not data:
        print("No tracked jobs yet.")
        return

    # Sort by updated_at desc; missing timestamps go last.
    items = sorted(
        data.items(),
        key=lambda kv: kv[1].get("updated_at") or "",
        reverse=True,
    )

    shown = 0
    for url, entry in items:
        if args.status and entry.get("status") != args.status:
            continue
        title = entry.get("title", "?")
        company = entry.get("company", "?")
        status = entry.get("status", "?")
        when = (entry.get("updated_at") or "")[:10]
        print(f"[{status:<14}] {when}  {title} @ {company}")
        print(f"    {url}")
        if entry.get("notes"):
            print(f"    notes: {entry['notes']}")
        shown += 1

    if shown == 0:
        filt = f" with status={args.status}" if args.status else ""
        print(f"No tracked jobs{filt}.")


def cmd_remove(args: argparse.Namespace) -> None:
    data = load()
    if args.url in data:
        del data[args.url]
        save(data)
        print(f"Removed {args.url}.")
    else:
        print(f"Not tracked: {args.url}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_mark = sub.add_parser("mark", help="Mark a job with a status")
    p_mark.add_argument("url")
    p_mark.add_argument("status", nargs="?", default="applied",
                        help="default: applied")
    p_mark.add_argument("--title", help="Optional job title (for nicer `list` output)")
    p_mark.add_argument("--company", help="Optional company name")
    p_mark.add_argument("--notes", help="Free-form notes")
    p_mark.set_defaults(fn=cmd_mark)

    p_list = sub.add_parser("list", help="List tracked jobs (newest first)")
    p_list.add_argument("status", nargs="?", default=None,
                        help="Optional status filter (e.g. `applied`)")
    p_list.set_defaults(fn=cmd_list)

    p_remove = sub.add_parser("remove", help="Remove a job from tracking")
    p_remove.add_argument("url")
    p_remove.set_defaults(fn=cmd_remove)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
