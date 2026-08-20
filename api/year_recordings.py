"""
Year playlist recordings for the 21Days mobile Recordings tab.

Config lists years with a YouTube playlist id and ordered sessions (video
counts). Videos are sorted oldest-first and sliced into those sessions.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from api.live_sessions import (  # noqa: E402
    _call_with_network_retry,
    _get_youtube,
    _parse_yt_time,
    _thumbnail_from_snippet,
    _watch_url,
    youtube_io_lock,
)

# videos.list accepts at most 50 ids per request.
_VIDEOS_LIST_CHUNK = 50

DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "year_playlists.json"
CACHE_TTL_SECONDS = 180

_cache: Dict[str, Any] = {"expires_at": 0.0, "payload": None}


def _load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = path or Path(
        os.environ.get("YEAR_PLAYLISTS_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    with config_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("year_playlists.json must be a JSON object")
    years = data.get("years")
    if not isinstance(years, list) or not years:
        raise ValueError("year_playlists.json must include a non-empty years array")
    return data


def _latest_year(config: Dict[str, Any]) -> Dict[str, Any]:
    years = [y for y in (config.get("years") or []) if isinstance(y, dict)]
    years.sort(key=lambda y: int(y.get("year") or 0), reverse=True)
    return years[0]


def _past_recording_ids(youtube, video_ids: List[str]) -> set:
    """
    Return video ids that are finished recordings (not upcoming / live).

    playlistItems does not expose liveBroadcastContent, so we resolve status
    via videos.list. Finished livestreams report liveBroadcastContent=none;
    scheduled future sessions report upcoming.
    """
    past: set = set()
    for i in range(0, len(video_ids), _VIDEOS_LIST_CHUNK):
        chunk = video_ids[i : i + _VIDEOS_LIST_CHUNK]
        if not chunk:
            continue
        ids = ",".join(chunk)

        def _fetch(ids_csv: str = ids) -> Dict[str, Any]:
            return (
                youtube.videos()
                .list(part="snippet,liveStreamingDetails", id=ids_csv)
                .execute()
            )

        res = _call_with_network_retry(_fetch)
        for item in res.get("items") or []:
            video_id = (item.get("id") or "").strip()
            if not video_id:
                continue
            snippet = item.get("snippet") or {}
            live_bc = (snippet.get("liveBroadcastContent") or "").strip().lower()
            if live_bc in {"upcoming", "live"}:
                continue
            past.add(video_id)
    return past


def _list_playlist_videos(youtube, playlist_id: str) -> List[Dict[str, Any]]:
    videos: List[Dict[str, Any]] = []
    page_token = None
    while True:
        kwargs: Dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        page_kwargs = dict(kwargs)

        def _fetch_page(k: Dict[str, Any] = page_kwargs) -> Dict[str, Any]:
            return youtube.playlistItems().list(**k).execute()

        res = _call_with_network_retry(_fetch_page)
        for item in res.get("items") or []:
            details = item.get("contentDetails") or {}
            snippet = item.get("snippet") or {}
            video_id = (details.get("videoId") or "").strip()
            if not video_id:
                continue
            title = (snippet.get("title") or "").strip()
            if title.lower() in {"deleted video", "private video"}:
                continue
            published = _parse_yt_time(
                details.get("videoPublishedAt") or snippet.get("publishedAt")
            )
            videos.append(
                {
                    "video_id": video_id,
                    "title": title or "Meditation",
                    "published_at": published,
                    "youtube_watch_url": _watch_url(video_id),
                    "youtube_thumbnail_url": _thumbnail_from_snippet(
                        snippet, video_id
                    ),
                }
            )
        page_token = res.get("nextPageToken")
        if not page_token:
            break

    if videos:
        past_ids = _past_recording_ids(
            youtube, [v["video_id"] for v in videos]
        )
        skipped = len(videos) - len(past_ids)
        if skipped > 0:
            print(
                f"year_recordings: omitting {skipped} upcoming/live playlist "
                "video(s); Recordings tab is past-only",
                flush=True,
            )
        videos = [v for v in videos if v["video_id"] in past_ids]

    videos.sort(
        key=lambda v: v["published_at"]
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    return videos


def _video_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    published = row.get("published_at")
    return {
        "video_id": row["video_id"],
        "title": row.get("title") or "",
        "published_at": published.isoformat()
        if isinstance(published, datetime)
        else None,
        "youtube_watch_url": row.get("youtube_watch_url") or "",
        "youtube_thumbnail_url": row.get("youtube_thumbnail_url") or "",
    }


def resolve_year_recordings(
    config_path: Optional[Path] = None,
    youtube_client=None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Latest configured year, playlist videos sliced into sessions.

    Returns {year, title, playlist_id, sessions: [...]}.
    """
    global _cache

    if use_cache and youtube_client is None:
        cached = _cache.get("payload")
        if cached is not None and time.time() < float(_cache.get("expires_at") or 0):
            return dict(cached)

    config = _load_config(config_path)
    year_cfg = _latest_year(config)
    playlist_id = (year_cfg.get("playlist_id") or "").strip()
    if not playlist_id:
        raise ValueError("Latest year is missing playlist_id")

    sessions_cfg = year_cfg.get("sessions") or []
    if not isinstance(sessions_cfg, list):
        raise ValueError("sessions must be an array")

    def _resolve() -> List[Dict[str, Any]]:
        youtube = youtube_client if youtube_client is not None else _get_youtube()
        return _list_playlist_videos(youtube, playlist_id)

    if youtube_client is not None:
        videos = _resolve()
    else:
        with youtube_io_lock:
            videos = _call_with_network_retry(_resolve)

    cursor = 0
    sessions_out: List[Dict[str, Any]] = []
    for spec in sessions_cfg:
        if not isinstance(spec, dict):
            continue
        count = int(spec.get("video_count") or 0)
        if count < 0:
            count = 0
        chunk = videos[cursor : cursor + count]
        cursor += count
        sessions_out.append(
            {
                "id": spec.get("id") or "",
                "label": spec.get("label") or "",
                "video_count": count,
                "videos": [_video_payload(row) for row in chunk],
            }
        )

    extra = len(videos) - cursor
    if extra > 0:
        print(
            f"year_recordings: omitting {extra} playlist videos beyond session counts",
            flush=True,
        )

    payload = {
        "year": int(year_cfg.get("year") or 0),
        "title": (year_cfg.get("title") or "").strip(),
        "playlist_id": playlist_id,
        "sessions": sessions_out,
    }

    if use_cache and youtube_client is None:
        _cache = {
            "expires_at": time.time() + CACHE_TTL_SECONDS,
            "payload": payload,
        }

    return payload
