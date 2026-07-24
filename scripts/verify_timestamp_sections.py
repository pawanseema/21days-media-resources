#!/usr/bin/env python3
"""
Compare Chroma timestamp sections to the live YouTube description.

Uses resources.verify_timestamp_sections (same parse path as ingest).

Usage (from project root, with venv):
  .venv/bin/python scripts/verify_timestamp_sections.py --video-id PRK5lR2MeAA
  .venv/bin/python scripts/verify_timestamp_sections.py --limit 5
  .venv/bin/python scripts/verify_timestamp_sections.py --mismatches-only
  .venv/bin/python scripts/verify_timestamp_sections.py --mismatches-only --kind summary_missing_in_chroma
  .venv/bin/python scripts/verify_timestamp_sections.py --apply-list   # print video_ids that fail (for re-ingest)

Exit code 1 if any verified video has mismatches (unless --no-fail).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from resources.verify_timestamp_sections import (  # noqa: E402
    format_report,
    list_chroma_video_ids,
    verify_video_against_youtube,
    verify_videos,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Chroma timestamp sections against YouTube descriptions."
    )
    parser.add_argument("--video-id", help="Check a single YouTube / Chroma video_id.")
    parser.add_argument("--limit", type=int, default=None, help="Max videos to check.")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N video_ids.")
    parser.add_argument(
        "--mismatches-only",
        action="store_true",
        help="Only print / return videos that fail (or error).",
    )
    parser.add_argument(
        "--kind",
        action="append",
        default=None,
        help="Only flag videos that have at least one mismatch of this kind "
        "(repeatable). Examples: summary_missing_in_chroma, title_mismatch.",
    )
    parser.add_argument(
        "--apply-list",
        action="store_true",
        help="Print only failing video_ids (one per line) for selective re-ingest.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON report list.",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Always exit 0 even when mismatches are found.",
    )
    args = parser.parse_args()

    if args.video_id:
        reports = [verify_video_against_youtube(args.video_id)]
        if args.mismatches_only:
            reports = [r for r in reports if not r.ok]
    else:
        all_ids = list_chroma_video_ids()
        print(
            f"Scanning Chroma videos: {len(all_ids)} total "
            f"(offset={args.offset}, limit={args.limit or 'all'})",
            file=sys.stderr,
        )
        reports = verify_videos(
            all_ids,
            limit=args.limit,
            offset=args.offset,
            mismatches_only=False,
        )
        if args.mismatches_only:
            reports = [r for r in reports if not r.ok]

    if args.kind:
        kinds = set(args.kind)
        filtered = []
        for r in reports:
            if r.error:
                filtered.append(r)
                continue
            if any(m.kind in kinds for m in r.mismatches):
                # Keep only matching mismatch kinds in the report copy for display
                r.mismatches = [m for m in r.mismatches if m.kind in kinds]
                r.ok = len(r.mismatches) == 0 and not r.error
                if not r.ok:
                    filtered.append(r)
        reports = filtered

    failing = [r for r in reports if not r.ok]

    if args.apply_list:
        for r in failing:
            print(r.video_id)
    elif args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        if not reports:
            print("No videos to report.")
        for r in reports:
            print(format_report(r, verbose=not r.ok))
            print()
        print(
            f"Summary: reported={len(reports)} failing={len(failing)} "
            f"(mismatches_only={args.mismatches_only})",
            file=sys.stderr,
        )

    if failing and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
