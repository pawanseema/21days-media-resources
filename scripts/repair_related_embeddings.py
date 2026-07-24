#!/usr/bin/env python3
"""
Backfill the More like this related collection with segment title+summary embeddings.

Creates/updates collection sahajayoga_21_days_videos_related (same ids as main
timestamp_section rows). recommend_related reads from this collection.

Usage (from project root, with venv):
  .venv/bin/python scripts/repair_related_embeddings.py
  .venv/bin/python scripts/repair_related_embeddings.py --limit 5
  .venv/bin/python scripts/repair_related_embeddings.py --video-id CO1vvOCoLjI
  .venv/bin/python scripts/repair_related_embeddings.py --apply --limit 5
  .venv/bin/python scripts/repair_related_embeddings.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from resources.video_processing import (  # noqa: E402
    RELATED_COLLECTION_NAME,
    collection,
    get_related_collection,
    segment_focus_text,
    upsert_related_section,
)


def load_sections(video_id: Optional[str] = None) -> List[Tuple[str, Dict]]:
    where = {"type": "timestamp_section"}
    if video_id:
        where = {
            "$and": [
                {"type": "timestamp_section"},
                {"video_id": video_id},
            ]
        }
    got = collection.get(where=where, include=["metadatas"])
    if not got or not got.get("ids"):
        return []
    rows = []
    for i, cid in enumerate(got["ids"]):
        meta = dict((got["metadatas"][i] if got.get("metadatas") else {}) or {})
        rows.append((cid, meta))
    return rows


def preview_row(cid: str, meta: Dict, focus: str, index: int, total: int) -> None:
    print()
    print("=" * 72)
    print(f"[{index}/{total}] {meta.get('video_id')} @ {meta.get('timestamp')}")
    print(f"id:          {cid}")
    print(f"video title: {meta.get('video_title', '')}")
    print(f"section:     {meta.get('section_title', '')}")
    print("-" * 72)
    print("FOCUS TEXT (to embed):")
    print(focus if focus else "(empty — will skip)")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill related-collection embeddings for More like this."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Embed and upsert into related collection (default is dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max sections to process (0=all).",
    )
    parser.add_argument("--offset", type=int, default=0, help="Skip first N sections.")
    parser.add_argument("--video-id", default="", help="Only this video_id.")
    args = parser.parse_args()

    video_id = (args.video_id or "").strip() or None
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Target related collection: {RELATED_COLLECTION_NAME}")
    print("Loading timestamp sections from main collection…")
    rows = load_sections(video_id=video_id)
    total = len(rows)
    print(f"  Loaded {total} timestamp_section row(s)")

    offset = max(0, args.offset)
    if offset:
        rows = rows[offset:]
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    print(f"  Processing {len(rows)} section(s) (offset={offset}, limit={args.limit or 'all'})")

    if not rows:
        print("Nothing to do.")
        return 0

    prepared = []
    skipped_empty = 0
    for cid, meta in rows:
        focus = segment_focus_text(
            meta.get("section_title", ""),
            meta.get("section_summary", ""),
        )
        if not focus.strip():
            skipped_empty += 1
        prepared.append((cid, meta, focus))

    for i, (cid, meta, focus) in enumerate(prepared, start=1):
        preview_row(cid, meta, focus, i, len(prepared))

    embeddable = sum(1 for _, _, f in prepared if f.strip())
    print(
        f"\nWould embed {embeddable} section(s); "
        f"skip {skipped_empty} with empty title+summary."
    )

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write related embeddings.")
        if total > offset + len(prepared):
            print(
                f"Next page: python scripts/repair_related_embeddings.py "
                f"--limit {args.limit or len(prepared)} --offset {offset + len(prepared)}"
            )
        return 0

    # Ensure collection exists
    get_related_collection()
    print(f"\nApplying related embeddings ({embeddable} OpenAI embedding call(s))…")
    ok = 0
    skipped = 0
    failed = 0
    for cid, meta, focus in prepared:
        if not focus.strip():
            skipped += 1
            continue
        try:
            upsert_related_section(cid, meta, focus_text=focus)
            ok += 1
            print(f"  OK  {meta.get('video_id')} @ {meta.get('timestamp')} ({cid})")
        except Exception as e:
            failed += 1
            print(f"  FAIL {cid}: {e}", file=sys.stderr)

    print(f"\nDone. upserted={ok} skipped_empty={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
