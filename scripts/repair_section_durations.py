#!/usr/bin/env python3
"""
Backfill section_duration_seconds (and video_duration_seconds) on Chroma rows.

For each video's timestamp_section rows (sorted by timestamp):
  - non-last: duration = next_timestamp - this_timestamp
  - last: duration = video_duration - this_timestamp (YouTube contentDetails)

Also writes video_duration_seconds onto video_context and each section.

Usage (from project root, with venv):
  .venv/bin/python scripts/repair_section_durations.py
  .venv/bin/python scripts/repair_section_durations.py --limit 3
  .venv/bin/python scripts/repair_section_durations.py --video-id CO1vvOCoLjI
  .venv/bin/python scripts/repair_section_durations.py --apply --limit 5
  .venv/bin/python scripts/repair_section_durations.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from resources.video_processing import (  # noqa: E402
    _chroma_safe_meta,
    assign_section_durations,
    collection,
    fetch_video_duration_seconds,
    timestamp_to_seconds,
)


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None or seconds < 0:
        return "(unknown)"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


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


def load_video_context(video_id: str) -> Optional[Tuple[str, Dict]]:
    vid = f"{video_id}_video"
    got = collection.get(ids=[vid], include=["metadatas"])
    if not got or not got.get("ids"):
        return None
    meta = dict((got["metadatas"][0] if got.get("metadatas") else {}) or {})
    return got["ids"][0], meta


def plan_video(
    video_id: str,
    items: List[Tuple[str, Dict]],
    duration_cache: Dict[str, int],
) -> Dict:
    items = sorted(
        items,
        key=lambda pair: timestamp_to_seconds(pair[1].get("timestamp", "")),
    )
    if video_id not in duration_cache:
        try:
            duration_cache[video_id] = fetch_video_duration_seconds(video_id)
        except Exception as e:
            print(f"  WARN YouTube duration fetch failed for {video_id}: {e}")
            duration_cache[video_id] = 0

    vdur = duration_cache[video_id]
    sections = []
    for cid, meta in items:
        sections.append({
            "chroma_id": cid,
            "timestamp": meta.get("timestamp", ""),
            "section_title": meta.get("section_title", ""),
            "old_duration": meta.get("section_duration_seconds"),
            "meta": meta,
        })

    # assign on a temp list with timestamp keys
    tmp = [{"timestamp": s["timestamp"]} for s in sections]
    assign_section_durations(tmp, video_duration_seconds=vdur)
    for s, t in zip(sections, tmp):
        s["new_duration"] = t.get("section_duration_seconds")

    return {
        "video_id": video_id,
        "video_title": (items[0][1].get("video_title") if items else "") or "",
        "video_duration_seconds": vdur,
        "sections": sections,
    }


def preview_plan(plan: Dict, index: int, total: int) -> None:
    print()
    print("=" * 72)
    print(f"[{index}/{total}] {plan['video_id']}")
    print(f"video title: {plan['video_title']}")
    print(
        f"video duration: {format_duration(plan['video_duration_seconds'])} "
        f"({plan['video_duration_seconds']}s)"
    )
    print("-" * 72)
    for s in plan["sections"]:
        old = s["old_duration"]
        new = s["new_duration"]
        changed = old != new
        flag = " *" if changed else ""
        print(
            f"  {s['timestamp']:>8}  "
            f"{format_duration(old):>10} → {format_duration(new):<10}  "
            f"{s['section_title'][:48]}{flag}"
        )
    print("=" * 72)


def apply_plan(plan: Dict) -> int:
    """Update Chroma metadata. Returns number of section rows updated."""
    updated = 0
    vdur = int(plan["video_duration_seconds"] or 0)

    ctx = load_video_context(plan["video_id"])
    if ctx:
        cid, meta = ctx
        meta = dict(meta)
        meta["video_duration_seconds"] = vdur
        collection.update(ids=[cid], metadatas=[_chroma_safe_meta(meta)])

    for s in plan["sections"]:
        meta = dict(s["meta"])
        meta["video_duration_seconds"] = vdur
        if s["new_duration"] is not None:
            meta["section_duration_seconds"] = int(s["new_duration"])
        else:
            meta.pop("section_duration_seconds", None)
        collection.update(
            ids=[s["chroma_id"]],
            metadatas=[_chroma_safe_meta(meta)],
        )
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill section/video duration metadata in Chroma."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write metadata updates (default is dry-run).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max videos to process (0=all).")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N videos.")
    parser.add_argument("--video-id", default="", help="Only this video_id.")
    args = parser.parse_args()

    video_id = (args.video_id or "").strip() or None
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Loading timestamp sections…")
    rows = load_sections(video_id=video_id)
    print(f"  Loaded {len(rows)} timestamp_section row(s)")

    by_video: Dict[str, List[Tuple[str, Dict]]] = defaultdict(list)
    for cid, meta in rows:
        vid = (meta.get("video_id") or "").strip()
        if vid:
            by_video[vid].append((cid, meta))

    video_ids = sorted(by_video.keys())
    total_videos = len(video_ids)
    print(f"  Videos: {total_videos}")

    offset = max(0, args.offset)
    if offset:
        video_ids = video_ids[offset:]
    if args.limit and args.limit > 0:
        video_ids = video_ids[: args.limit]
    print(f"  Processing {len(video_ids)} video(s) (offset={offset}, limit={args.limit or 'all'})")

    if not video_ids:
        print("Nothing to do.")
        return 0

    duration_cache: Dict[str, int] = {}
    plans = []
    for vid in video_ids:
        plans.append(plan_video(vid, by_video[vid], duration_cache))

    for i, plan in enumerate(plans, start=1):
        preview_plan(plan, i, len(plans))

    if not args.apply:
        print(
            f"\nDry-run only. Would update durations for {len(plans)} video(s) "
            f"({sum(len(p['sections']) for p in plans)} sections)."
        )
        print("Re-run with --apply to write metadata.")
        if total_videos > offset + len(plans):
            print(
                f"Next page: python scripts/repair_section_durations.py "
                f"--limit {args.limit or len(plans)} --offset {offset + len(plans)}"
            )
        return 0

    print(f"\nApplying duration updates for {len(plans)} video(s)…")
    ok = 0
    failed = 0
    for plan in plans:
        try:
            n = apply_plan(plan)
            ok += 1
            print(f"  OK  {plan['video_id']} ({n} sections)")
        except Exception as e:
            failed += 1
            print(f"  FAIL {plan['video_id']}: {e}", file=sys.stderr)

    print(f"\nDone. videos_ok={ok} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
