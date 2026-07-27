"""
Live / upcoming meditation sessions for the 21Days mobile app.

Resolves against configured YouTube channels (live preferred, else soonest
upcoming within N hours). Schedule is not invented from a local clock —
if YouTube has nothing, the API returns session: null.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import load_yt_api_key  # noqa: E402

DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "live_sessions.json"
DEFAULT_ZOOM_URL = "https://us06web.zoom.us/j/2121217171"
DEFAULT_WITHIN_HOURS = 24
CACHE_TTL_SECONDS = 45

_youtube_client = None
_cache: Dict[str, Any] = {"expires_at": 0.0, "payload": None}


def _load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = path or Path(
        os.environ.get("LIVE_SESSIONS_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    with config_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("live_sessions.json must be a JSON object")
    return data


def _get_youtube():
    global _youtube_client
    if _youtube_client is None:
        from googleapiclient.discovery import build

        key = load_yt_api_key()
        if not key:
            raise ValueError("YouTube API key is not configured")
        _youtube_client = build("youtube", "v3", developerKey=key)
    return _youtube_client


def _parse_yt_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _thumbnail_from_snippet(snippet: Dict[str, Any], video_id: str = "") -> str:
    thumbs = (snippet or {}).get("thumbnails") or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbs.get(key) or {}).get("url")
        if url:
            return url
    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return ""


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _search_channel_broadcasts(
    youtube,
    channel_id: str,
    event_type: str,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """
    Search a channel for live or upcoming broadcasts.

    event_type: "live" | "upcoming"
    """
    res = (
        youtube.search()
        .list(
            part="snippet",
            channelId=channel_id,
            type="video",
            eventType=event_type,
            order="date",
            maxResults=max_results,
        )
        .execute()
    )
    out = []
    for item in res.get("items") or []:
        vid = ((item.get("id") or {}).get("videoId") or "").strip()
        if not vid:
            continue
        snippet = item.get("snippet") or {}
        out.append(
            {
                "video_id": vid,
                "title": snippet.get("title") or "",
                "channel_id": snippet.get("channelId") or channel_id,
                "channel_title": snippet.get("channelTitle") or "",
                "thumbnail_url": _thumbnail_from_snippet(snippet, vid),
                "published_at": snippet.get("publishedAt"),
            }
        )
    return out


def _enrich_live_details(youtube, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach liveStreamingDetails (scheduled/actual start) via videos.list."""
    if not rows:
        return []
    ids = [r["video_id"] for r in rows]
    res = (
        youtube.videos()
        .list(part="snippet,liveStreamingDetails", id=",".join(ids))
        .execute()
    )
    by_id = {item["id"]: item for item in (res.get("items") or [])}
    enriched = []
    for row in rows:
        item = by_id.get(row["video_id"])
        if not item:
            continue
        snippet = item.get("snippet") or {}
        live = item.get("liveStreamingDetails") or {}
        starts = (
            _parse_yt_time(live.get("actualStartTime"))
            or _parse_yt_time(live.get("scheduledStartTime"))
        )
        ends = _parse_yt_time(live.get("actualEndTime")) or _parse_yt_time(
            live.get("scheduledEndTime")
        )
        enriched.append(
            {
                **row,
                "title": snippet.get("title") or row.get("title") or "",
                "channel_title": snippet.get("channelTitle")
                or row.get("channel_title")
                or "",
                "thumbnail_url": _thumbnail_from_snippet(snippet, row["video_id"])
                or row.get("thumbnail_url")
                or "",
                "starts_at": starts,
                "ends_at": ends,
                "live_broadcast_content": snippet.get("liveBroadcastContent") or "",
            }
        )
    return enriched


def _channel_meta(config: Dict[str, Any], channel_id: str) -> Dict[str, str]:
    for ch in config.get("channels") or []:
        if (ch or {}).get("id") == channel_id:
            return {
                "id": channel_id,
                "title": (ch.get("title") or "").strip(),
                "handle": (ch.get("handle") or "").strip(),
            }
    return {"id": channel_id, "title": "", "handle": ""}


def _session_payload(
    *,
    status: str,
    source: str,
    row: Dict[str, Any],
    config: Dict[str, Any],
    channel_meta: Dict[str, str],
) -> Dict[str, Any]:
    zoom = (config.get("zoom_meeting_url") or DEFAULT_ZOOM_URL).strip()
    starts = row.get("starts_at")
    ends = row.get("ends_at")
    video_id = row["video_id"]
    return {
        "id": f"{status}_{video_id}",
        "status": status,
        "source": source,
        "video_id": video_id,
        "title": row.get("title") or "",
        "channel_id": channel_meta.get("id") or row.get("channel_id") or "",
        "channel_title": channel_meta.get("title")
        or row.get("channel_title")
        or "",
        "channel_handle": channel_meta.get("handle") or "",
        "starts_at": starts.isoformat() if isinstance(starts, datetime) else None,
        "ends_at": ends.isoformat() if isinstance(ends, datetime) else None,
        "youtube_live_url": _watch_url(video_id),
        "youtube_thumbnail_url": row.get("thumbnail_url") or "",
        "zoom_meeting_url": zoom,
    }


def _find_live_session(youtube, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    channels = config.get("channels") or []
    for ch in channels:
        channel_id = (ch or {}).get("id") or ""
        if not channel_id:
            continue
        hits = _search_channel_broadcasts(youtube, channel_id, "live", max_results=1)
        if not hits:
            continue
        enriched = _enrich_live_details(youtube, hits)
        if not enriched:
            # Still return search hit if videos.list failed
            row = hits[0]
            row["starts_at"] = None
            row["ends_at"] = None
            return _session_payload(
                status="live",
                source="youtube_live",
                row=row,
                config=config,
                channel_meta=_channel_meta(config, channel_id),
            )
        row = enriched[0]
        return _session_payload(
            status="live",
            source="youtube_live",
            row=row,
            config=config,
            channel_meta=_channel_meta(config, channel_id),
        )
    return None


def _find_soonest_upcoming(
    youtube,
    config: Dict[str, Any],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Per channel: take the soonest upcoming broadcast.
    Globally: return the soonest among those within upcoming_within_hours.
    """
    within_hours = int(config.get("upcoming_within_hours") or DEFAULT_WITHIN_HOURS)
    horizon = now + timedelta(hours=within_hours)
    per_channel: List[Dict[str, Any]] = []

    for ch in config.get("channels") or []:
        channel_id = (ch or {}).get("id") or ""
        if not channel_id:
            continue
        hits = _search_channel_broadcasts(
            youtube, channel_id, "upcoming", max_results=5
        )
        enriched = _enrich_live_details(youtube, hits)
        candidates = []
        for row in enriched:
            starts = row.get("starts_at")
            if not isinstance(starts, datetime):
                continue
            if now <= starts <= horizon:
                candidates.append(row)
        if not candidates:
            continue
        candidates.sort(key=lambda r: r["starts_at"])
        best = candidates[0]
        per_channel.append(
            _session_payload(
                status="upcoming",
                source="youtube_upcoming",
                row=best,
                config=config,
                channel_meta=_channel_meta(config, channel_id),
            )
        )

    if not per_channel:
        return None

    def _start_key(session: Dict[str, Any]):
        return session.get("starts_at") or ""

    per_channel.sort(key=_start_key)
    return per_channel[0]


def resolve_next_session(
    now: Optional[datetime] = None,
    config_path: Optional[Path] = None,
    youtube_client=None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Resolve current live or soonest upcoming YouTube session.

    Returns {"server_time": ..., "session": <obj>|null}.
    """
    global _cache
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    server_time = current.isoformat()

    if use_cache and now is None and youtube_client is None:
        cached = _cache.get("payload")
        if cached is not None and time.time() < float(_cache.get("expires_at") or 0):
            # Refresh server_time on cached payloads
            out = dict(cached)
            out["server_time"] = server_time
            return out

    config = _load_config(config_path)
    youtube = youtube_client if youtube_client is not None else _get_youtube()

    session = _find_live_session(youtube, config)
    if session is None:
        session = _find_soonest_upcoming(youtube, config, current)

    payload = {
        "server_time": server_time,
        "session": session,
    }

    if use_cache and now is None and youtube_client is None:
        _cache = {
            "expires_at": time.time() + CACHE_TTL_SECONDS,
            "payload": payload,
        }

    return payload


# Back-compat alias used by earlier drafts / callers
resolve_live_sessions = resolve_next_session
