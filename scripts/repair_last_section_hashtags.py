#!/usr/bin/env python3
"""
Repair last timestamp-section summaries that absorbed trailing YouTube hashtags.

Scans local Chroma, finds the chronologically last timestamp_section per video,
and if section_summary still contains #tags, strips them and (with --apply)
rebuilds the stored document + embedding so search / More like this match a
fresh ingest with the fixed parse_timestamps.

Usage (from project root, with venv):
  .venv/bin/python scripts/repair_last_section_hashtags.py --limit 3
  .venv/bin/python scripts/repair_last_section_hashtags.py --limit 3 --offset 3
  .venv/bin/python scripts/repair_last_section_hashtags.py --video-id CO1vvOCoLjI
  .venv/bin/python scripts/repair_last_section_hashtags.py --apply --limit 5
  .venv/bin/python scripts/repair_last_section_hashtags.py --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import after path setup; uses same Chroma + OpenAI wiring as ingest.
from resources.video_processing import (  # noqa: E402
    build_embedding_text,
    collection,
    get_embedding,
)

HASHTAG_TOKEN_RE = re.compile(r"#\w+")
HASHTAG_ONLY_RE = re.compile(r"^(?:#\w+\s*)+$")


def timestamp_to_seconds(ts: str) -> int:
    """Parse MM:SS or HH:MM:SS to seconds for ordering."""
    if not ts:
        return -1
    parts = str(ts).strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return -1
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return -1


def clean_summary_hashtags(summary: str) -> str:
    """
    Remove hashtag tokens from a section summary (same idea as parse_timestamps).

    Trailing hashtag-only lines become empty; mixed prose keeps the prose.
    """
    text = (summary or "").strip()
    if not text:
        return ""
    if HASHTAG_ONLY_RE.match(text):
        return ""
    cleaned = HASHTAG_TOKEN_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    # Drop leftover punctuation clumps from removed tags
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return cleaned.strip()


def needs_repair(summary: str) -> bool:
    return "#" in (summary or "")


def load_timestamp_sections(
    video_id: Optional[str] = None,
) -> List[Tuple[str, Dict]]:
    """Return (chroma_id, metadata) for timestamp_section rows."""
    where = {"type": "timestamp_section"}
    if video_id:
        where = {
            "$and": [
                {"type": "timestamp_section"},
                {"video_id": video_id},
            ]
        }
    got = collection.get(where=where, include=["metadatas", "documents"])
    if not got or not got.get("ids"):
        return []
    rows = []
    for i, cid in enumerate(got["ids"]):
        meta = (got["metadatas"][i] if got.get("metadatas") else {}) or {}
        # Keep document available for dry-run preview via meta side channel
        doc = ""
        if got.get("documents") and got["documents"][i] is not None:
            doc = got["documents"][i]
        meta = dict(meta)
        meta["_document"] = doc
        rows.append((cid, meta))
    return rows


def find_dirty_last_sections(
    rows: List[Tuple[str, Dict]],
) -> List[Tuple[str, Dict, str]]:
    """
    Per video_id, take the last timestamp section; keep those whose summary
    still contains hashtags.

    Returns list of (chroma_id, meta, cleaned_summary).
    """
    by_video: Dict[str, List[Tuple[str, Dict]]] = defaultdict(list)
    for cid, meta in rows:
        vid = (meta.get("video_id") or "").strip()
        if not vid:
            continue
        by_video[vid].append((cid, meta))

    dirty = []
    for vid, items in by_video.items():
        items.sort(
            key=lambda pair: timestamp_to_seconds(pair[1].get("timestamp", ""))
        )
        cid, meta = items[-1]
        old_summary = meta.get("section_summary") or ""
        if not needs_repair(old_summary):
            continue
        new_summary = clean_summary_hashtags(old_summary)
        dirty.append((cid, meta, new_summary))
    return dirty


def preview_row(cid: str, meta: Dict, new_summary: str, index: int, total: int) -> None:
    old = meta.get("section_summary") or ""
    title = meta.get("section_title") or ""
    video_title = meta.get("video_title") or ""
    vid = meta.get("video_id") or ""
    ts = meta.get("timestamp") or ""
    watch_url = meta.get("video_url") or (
        f"https://www.youtube.com/watch?v={vid}" if vid else ""
    )
    print()
    print("=" * 72)
    print(f"[{index}/{total}] {vid} @ {ts}")
    print(f"id:          {cid}")
    print(f"video title: {video_title}")
    if watch_url:
        print(f"youtube:     {watch_url}")
    print(f"section:     {title}")
    print("-" * 72)
    print("BEFORE summary:")
    print(old if old else "(empty)")
    print("-" * 72)
    print("AFTER summary:")
    print(new_summary if new_summary else "(empty)")
    print("=" * 72)


def apply_repair(cid: str, meta: Dict, new_summary: str) -> None:
    """Update metadata, document, and embedding for one section."""
    row = {k: v for k, v in meta.items() if not k.startswith("_")}
    row["section_summary"] = new_summary
    row["type"] = row.get("type") or "timestamp_section"
    embedding_text = build_embedding_text(row)
    embedding = get_embedding(embedding_text)
    meta_for_chroma = {k: v for k, v in row.items() if k not in ("embedding", "embedding_text")}
    collection.update(
        ids=[cid],
        documents=[embedding_text],
        embeddings=[embedding],
        metadatas=[meta_for_chroma],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair last-section summaries polluted by trailing hashtags."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write cleaned summary + re-embed into Chroma (default is dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only consider the first N dirty last-sections (dry-run or apply). 0 = all.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N dirty rows before applying --limit (for paging dry-runs).",
    )
    parser.add_argument(
        "--video-id",
        default="",
        help="Only consider this YouTube video_id.",
    )
    args = parser.parse_args()

    video_id = (args.video_id or "").strip() or None
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Scanning timestamp sections…")
    rows = load_timestamp_sections(video_id=video_id)
    print(f"  Loaded {len(rows)} timestamp_section row(s)")

    dirty = find_dirty_last_sections(rows)
    total_dirty = len(dirty)
    print(f"  Dirty last sections (summary contains #): {total_dirty}")

    if not dirty:
        print("Nothing to repair.")
        return 0

    offset = max(0, args.offset)
    if offset:
        dirty = dirty[offset:]
        print(f"  After --offset {offset}: {len(dirty)} remaining")

    if args.limit and args.limit > 0:
        dirty = dirty[: args.limit]
        print(f"  After --limit {args.limit}: showing/processing {len(dirty)} row(s)")

    if not dirty:
        print("Nothing left after --offset/--limit.")
        return 0

    print(f"\nFull before/after summaries ({len(dirty)} of {total_dirty} dirty):")
    for i, (cid, meta, new_summary) in enumerate(dirty, start=1):
        preview_row(cid, meta, new_summary, index=i, total=len(dirty))

    if not args.apply:
        print(
            f"\nDry-run only. Would re-embed these {len(dirty)} section(s) "
            f"(~{len(dirty)} OpenAI embedding call(s); {total_dirty} dirty total)."
        )
        print("Re-run with --apply (same --limit/--offset/--video-id) to write changes.")
        if total_dirty > len(dirty):
            next_offset = offset + len(dirty)
            print(
                f"Next page: python scripts/repair_last_section_hashtags.py "
                f"--limit {args.limit or len(dirty)} --offset {next_offset}"
            )
        return 0

    print(f"\nApplying repair to {len(dirty)} section(s)…")
    ok = 0
    failed = 0
    for cid, meta, new_summary in dirty:
        try:
            apply_repair(cid, meta, new_summary)
            ok += 1
            print(f"  OK  {meta.get('video_id')} @ {meta.get('timestamp')} ({cid})")
        except Exception as e:
            failed += 1
            print(f"  FAIL {cid}: {e}", file=sys.stderr)

    print(f"\nDone. updated={ok} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
