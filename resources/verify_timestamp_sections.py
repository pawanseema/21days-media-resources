"""
Verify Chroma timestamp_section rows against the live YouTube description.

Uses the same clean_description + parse_timestamps path as ingest, so a pass
means stored chapters (timestamp, title, summary) still match what a fresh
ingest would write from today's description.

Reusable from:
  - post-ingest checks (process_video_by_id / process_playlist)
  - scripts/verify_timestamp_sections.py (one video or full collection scan)
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from resources.video_processing import (
    clean_description,
    collection as default_collection,
    parse_timestamps,
    timestamp_to_seconds,
    youtube,
)

# Default on for post-ingest; set VERIFY_TIMESTAMP_SECTIONS=false to skip.
_VERIFY_ENV = "VERIFY_TIMESTAMP_SECTIONS"


def verify_after_ingest_enabled() -> bool:
    raw = os.environ.get(_VERIFY_ENV, "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def normalize_timestamp(ts: str) -> str:
    """Normalize MM:SS / HH:MM:SS for comparison (e.g. 4:04 vs 04:04)."""
    if ts is None:
        return ""
    text = str(ts).strip()
    if not text:
        return ""
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return text
    if len(nums) == 2:
        return f"{nums[0]}:{nums[1]:02d}"
    if len(nums) == 3:
        return f"{nums[0]}:{nums[1]:02d}:{nums[2]:02d}"
    return text


def normalize_text(value: Optional[str]) -> str:
    """Collapse whitespace for stable title/summary comparison."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


@dataclass
class SectionSnapshot:
    timestamp: str
    section_title: str
    section_summary: str

    @property
    def key(self) -> str:
        return normalize_timestamp(self.timestamp)


@dataclass
class SectionMismatch:
    kind: str
    timestamp: str = ""
    youtube_title: str = ""
    chroma_title: str = ""
    youtube_summary: str = ""
    chroma_summary: str = ""
    detail: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class VideoVerifyReport:
    video_id: str
    video_title: str = ""
    ok: bool = True
    youtube_count: int = 0
    chroma_count: int = 0
    mismatches: List[SectionMismatch] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "video_id": self.video_id,
            "video_title": self.video_title,
            "ok": self.ok,
            "youtube_count": self.youtube_count,
            "chroma_count": self.chroma_count,
            "mismatch_count": len(self.mismatches),
            "mismatches": [m.to_dict() for m in self.mismatches],
            "error": self.error,
        }


def fetch_youtube_sections(video_id: str) -> Tuple[str, List[SectionSnapshot]]:
    """
    Fetch live description and parse timestamp chapters (same as ingest).

    Returns (video_title, sections).
    """
    video_id = (video_id or "").strip()
    if not video_id:
        raise ValueError("video_id is required")

    res = youtube.videos().list(part="snippet", id=video_id).execute()
    items = res.get("items") or []
    if not items:
        raise ValueError(f"Video not found on YouTube: {video_id}")

    snippet = items[0].get("snippet") or {}
    title = snippet.get("title") or f"Video {video_id}"
    desc = clean_description(snippet.get("description") or "")
    parsed = parse_timestamps(desc)
    sections = [
        SectionSnapshot(
            timestamp=normalize_timestamp(p.get("timestamp", "")),
            section_title=normalize_text(p.get("section_title", "")),
            section_summary=normalize_text(p.get("section_summary", "")),
        )
        for p in parsed
    ]
    return title, sections


def load_chroma_sections(video_id: str, chroma_collection=None) -> List[SectionSnapshot]:
    """Load timestamp_section rows for a video from Chroma."""
    video_id = (video_id or "").strip()
    coll = chroma_collection if chroma_collection is not None else default_collection
    got = coll.get(
        where={
            "$and": [
                {"video_id": video_id},
                {"type": "timestamp_section"},
            ]
        },
        include=["metadatas"],
    )
    if not got or not got.get("ids"):
        return []

    sections = []
    for meta in got.get("metadatas") or []:
        meta = meta or {}
        sections.append(
            SectionSnapshot(
                timestamp=normalize_timestamp(meta.get("timestamp", "")),
                section_title=normalize_text(meta.get("section_title", "")),
                section_summary=normalize_text(meta.get("section_summary", "")),
            )
        )
    sections.sort(key=lambda s: timestamp_to_seconds(s.timestamp))
    return sections


def compare_sections(
    youtube_sections: List[SectionSnapshot],
    chroma_sections: List[SectionSnapshot],
) -> List[SectionMismatch]:
    """
    Diff YouTube-parsed chapters vs Chroma rows.

    Match key is normalized timestamp. Reports missing sides and title/summary
    differences for shared timestamps.
    """
    yt_by_key: Dict[str, SectionSnapshot] = {}
    for s in youtube_sections:
        # Prefer first occurrence if duplicate timestamps (rare)
        yt_by_key.setdefault(s.key, s)

    ch_by_key: Dict[str, SectionSnapshot] = {}
    for s in chroma_sections:
        ch_by_key.setdefault(s.key, s)

    mismatches: List[SectionMismatch] = []
    all_keys = sorted(
        set(yt_by_key) | set(ch_by_key),
        key=lambda k: timestamp_to_seconds(k) if k else -1,
    )

    for key in all_keys:
        yt = yt_by_key.get(key)
        ch = ch_by_key.get(key)
        if yt and not ch:
            mismatches.append(
                SectionMismatch(
                    kind="missing_in_chroma",
                    timestamp=key,
                    youtube_title=yt.section_title,
                    youtube_summary=yt.section_summary,
                    detail="Present on YouTube, missing in Chroma",
                )
            )
            continue
        if ch and not yt:
            mismatches.append(
                SectionMismatch(
                    kind="missing_on_youtube",
                    timestamp=key,
                    chroma_title=ch.section_title,
                    chroma_summary=ch.section_summary,
                    detail="Present in Chroma, not in current YouTube parse",
                )
            )
            continue
        assert yt is not None and ch is not None
        if yt.section_title != ch.section_title:
            mismatches.append(
                SectionMismatch(
                    kind="title_mismatch",
                    timestamp=key,
                    youtube_title=yt.section_title,
                    chroma_title=ch.section_title,
                    youtube_summary=yt.section_summary,
                    chroma_summary=ch.section_summary,
                    detail="section_title differs",
                )
            )
        if yt.section_summary != ch.section_summary:
            kind = "summary_mismatch"
            if yt.section_summary and not ch.section_summary:
                kind = "summary_missing_in_chroma"
            elif ch.section_summary and not yt.section_summary:
                kind = "summary_extra_in_chroma"
            mismatches.append(
                SectionMismatch(
                    kind=kind,
                    timestamp=key,
                    youtube_title=yt.section_title,
                    chroma_title=ch.section_title,
                    youtube_summary=yt.section_summary,
                    chroma_summary=ch.section_summary,
                    detail="section_summary differs",
                )
            )
    return mismatches


def verify_video_against_youtube(
    video_id: str,
    chroma_collection=None,
) -> VideoVerifyReport:
    """
    Compare one video's Chroma timestamp sections to live YouTube chapters.
    """
    video_id = (video_id or "").strip()
    report = VideoVerifyReport(video_id=video_id)
    try:
        title, yt_sections = fetch_youtube_sections(video_id)
        report.video_title = title
        report.youtube_count = len(yt_sections)
    except Exception as e:
        report.ok = False
        report.error = str(e)
        return report

    try:
        ch_sections = load_chroma_sections(video_id, chroma_collection=chroma_collection)
        report.chroma_count = len(ch_sections)
    except Exception as e:
        report.ok = False
        report.error = str(e)
        return report

    if report.chroma_count == 0:
        report.ok = False
        report.mismatches.append(
            SectionMismatch(
                kind="no_chroma_sections",
                detail="No timestamp_section rows in Chroma for this video_id",
            )
        )
        return report

    report.mismatches = compare_sections(yt_sections, ch_sections)
    report.ok = len(report.mismatches) == 0
    return report


def list_chroma_video_ids(chroma_collection=None) -> List[str]:
    """Unique video_ids that have at least one timestamp_section."""
    coll = chroma_collection if chroma_collection is not None else default_collection
    got = coll.get(where={"type": "timestamp_section"}, include=["metadatas"])
    if not got or not got.get("ids"):
        return []
    ids = []
    seen = set()
    for meta in got.get("metadatas") or []:
        vid = str((meta or {}).get("video_id") or "").strip()
        if vid and vid not in seen:
            seen.add(vid)
            ids.append(vid)
    return sorted(ids)


def verify_videos(
    video_ids: Optional[List[str]] = None,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    chroma_collection=None,
    mismatches_only: bool = False,
) -> List[VideoVerifyReport]:
    """
    Verify many videos. If video_ids is None, scan all Chroma video_ids.
    """
    if video_ids is None:
        video_ids = list_chroma_video_ids(chroma_collection=chroma_collection)
    else:
        video_ids = [str(v).strip() for v in video_ids if str(v).strip()]

    offset = max(0, int(offset or 0))
    sliced = video_ids[offset:]
    if limit is not None:
        sliced = sliced[: max(0, int(limit))]

    reports = []
    for vid in sliced:
        report = verify_video_against_youtube(vid, chroma_collection=chroma_collection)
        if mismatches_only and report.ok:
            continue
        reports.append(report)
    return reports


def format_report(report: VideoVerifyReport, *, verbose: bool = True) -> str:
    """Human-readable multi-line report for one video."""
    lines = []
    status = "OK" if report.ok else "MISMATCH"
    title = report.video_title or "(unknown title)"
    lines.append(f"[{status}] {report.video_id} — {title}")
    if report.error:
        lines.append(f"  error: {report.error}")
        return "\n".join(lines)
    lines.append(
        f"  youtube_sections={report.youtube_count}  chroma_sections={report.chroma_count}  "
        f"issues={len(report.mismatches)}"
    )
    if not verbose or report.ok:
        return "\n".join(lines)
    for m in report.mismatches:
        lines.append(f"  - {m.kind} @ {m.timestamp or '(n/a)'}")
        if m.detail:
            lines.append(f"      {m.detail}")
        if m.kind in (
            "summary_mismatch",
            "summary_missing_in_chroma",
            "summary_extra_in_chroma",
            "title_mismatch",
            "missing_in_chroma",
            "missing_on_youtube",
        ):
            if m.youtube_title or m.chroma_title:
                lines.append(f"      youtube_title: {m.youtube_title!r}")
                lines.append(f"      chroma_title:  {m.chroma_title!r}")
            if m.kind.startswith("summary") or m.kind == "title_mismatch":
                lines.append(f"      youtube_summary: {m.youtube_summary!r}")
                lines.append(f"      chroma_summary:  {m.chroma_summary!r}")
            elif m.kind == "missing_in_chroma" and m.youtube_summary:
                lines.append(f"      youtube_summary: {m.youtube_summary!r}")
            elif m.kind == "missing_on_youtube" and m.chroma_summary:
                lines.append(f"      chroma_summary: {m.chroma_summary!r}")
    return "\n".join(lines)


def assert_video_matches_youtube(video_id: str, chroma_collection=None) -> VideoVerifyReport:
    """
    Verify and raise ValueError with details if mismatched or errored.
    Intended for post-ingest enforcement.
    """
    report = verify_video_against_youtube(video_id, chroma_collection=chroma_collection)
    if report.ok:
        return report
    raise ValueError(
        "Timestamp section verify failed for "
        f"{video_id}:\n{format_report(report, verbose=True)}"
    )
