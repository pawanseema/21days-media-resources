"""
Live / upcoming meditation sessions for the 21Days mobile app.

Resolves against configured YouTube channels (live preferred, else soonest
upcoming within N hours). Schedule is not invented from a local clock —
if YouTube has nothing, the API returns session: null.

YouTube search.list costs 100 quota units and has a separate daily Search
Queries cap. Live/recent therefore use the channel uploads playlist +
videos.list (~1 unit each) instead of search.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import load_yt_api_key  # noqa: E402

DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "live_sessions.json"
DEFAULT_ZOOM_URL = "https://us06web.zoom.us/j/2121217171"
DEFAULT_WITHIN_HOURS = 72
DEFAULT_RECENT_WITHIN_HOURS = 72
SNAPSHOT_TTL_SECONDS = 180
UPLOADS_MAX_RESULTS = 15
YOUTUBE_HTTP_TIMEOUT_SECONDS = 12

_tls = threading.local()
_snapshot_cache: Dict[str, Any] = {
    "expires_at": 0.0,
    "rows_by_channel": None,
}
# Serialize YouTube HTTP across Live / recent / recordings so overlapping
# Flask threads do not share httplib2 SSL state and 503 both clients.
youtube_io_lock = threading.Lock()

# Transient local bind / routing failures seen on macOS when contacting Google APIs
_TRANSIENT_ERRNOS = {48, 49, 51, 54, 60, 65}  # addr in use, can't assign, reset, timeout, no route
_TRANSIENT_MESSAGE_NEEDLES = (
    "can't assign requested address",
    "errno 49",
    "timed out",
    "timeout",
    "record layer failure",
    "ssl syscall error",
    "connection reset",
    "broken pipe",
    "temporarily unavailable",
)
_QUOTA_MESSAGE_NEEDLES = (
    "quota exceeded",
    "quotaexceeded",
    "daily limit exceeded",
)
_KEY_QUERY_RE = re.compile(r"(key=)[^&\s\"']+", re.IGNORECASE)
_AIZA_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")


def _load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = path or Path(
        os.environ.get("LIVE_SESSIONS_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    with config_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("live_sessions.json must be a JSON object")
    return data


def _reset_youtube_client() -> None:
    _tls.youtube_client = None


def _get_youtube():
    """
    Per-thread YouTube client.

    google-api-python-client / httplib2 are not thread-safe. Sharing one client
    across Flask worker threads causes SSL record-layer failures and timeouts
    when Live, recent, and recordings hit YouTube at once.
    """
    client = getattr(_tls, "youtube_client", None)
    if client is None:
        import httplib2
        from googleapiclient.discovery import build

        key = load_yt_api_key()
        if not key:
            raise ValueError("YouTube API key is not configured")
        http = httplib2.Http(timeout=YOUTUBE_HTTP_TIMEOUT_SECONDS)
        client = build(
            "youtube",
            "v3",
            developerKey=key,
            http=http,
            cache_discovery=False,
        )
        _tls.youtube_client = client
    return client


def redact_youtube_error(exc: BaseException | str) -> str:
    """Strip API keys from YouTube client error strings before logging."""
    text = str(exc)
    text = _KEY_QUERY_RE.sub(r"\1REDACTED", text)
    return _AIZA_RE.sub("REDACTED", text)


def _walk_exceptions(exc: BaseException):
    cur: Optional[BaseException] = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def _http_status(exc: BaseException) -> Optional[int]:
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        return None
    if not isinstance(exc, HttpError):
        return None
    return getattr(getattr(exc, "resp", None), "status", None)


def is_youtube_quota_error(exc: BaseException) -> bool:
    """Daily quota / search-query cap — do not retry; it will not recover today."""
    for cur in _walk_exceptions(exc):
        msg = str(cur).lower()
        if any(needle in msg for needle in _QUOTA_MESSAGE_NEEDLES):
            return True
        status = _http_status(cur)
        if status == 403 and "quota" in msg:
            return True
    return False


def _is_transient_http_error(exc: BaseException) -> bool:
    if is_youtube_quota_error(exc):
        return False
    status = _http_status(exc)
    # Do not retry 429: daily search quota is often labeled rateLimitExceeded.
    return status in {500, 502, 503, 504}


def _is_transient_network_error(exc: BaseException) -> bool:
    if is_youtube_quota_error(exc):
        return False
    for cur in _walk_exceptions(exc):
        if isinstance(cur, (TimeoutError, socket.timeout, ssl.SSLError)):
            return True
        if _is_transient_http_error(cur):
            return True
        if isinstance(cur, OSError) and getattr(cur, "errno", None) in _TRANSIENT_ERRNOS:
            return True
        msg = str(cur).lower()
        if any(needle in msg for needle in _TRANSIENT_MESSAGE_NEEDLES):
            if "quota" in msg:
                return False
            return True
    return False


def is_transient_youtube_error(exc: BaseException) -> bool:
    """Public alias used by Flask to map YouTube blips to HTTP 503."""
    return _is_transient_network_error(exc)


def _call_with_network_retry(fn: Callable[[], Any], attempts: int = 3) -> Any:
    """Retry YouTube HTTP calls that fail with transient local network errors."""
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — narrow by errno below
            last = exc
            if (
                is_youtube_quota_error(exc)
                or not _is_transient_network_error(exc)
                or attempt + 1 >= attempts
            ):
                raise
            _reset_youtube_client()
            time.sleep(0.4 * (2 ** attempt))
    assert last is not None
    raise last


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


def _uploads_playlist_id(channel_id: str) -> str:
    cid = (channel_id or "").strip()
    if cid.startswith("UC") and len(cid) > 2:
        return "UU" + cid[2:]
    return ""


def _list_channel_uploads(
    youtube,
    channel_id: str,
    max_results: int = UPLOADS_MAX_RESULTS,
) -> List[Dict[str, Any]]:
    """Newest uploads for a channel (includes current live when YouTube lists it)."""
    playlist_id = _uploads_playlist_id(channel_id)
    if not playlist_id:
        return []

    def _fetch() -> Dict[str, Any]:
        return (
            youtube.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=max_results,
            )
            .execute()
        )

    res = _call_with_network_retry(_fetch)
    out: List[Dict[str, Any]] = []
    for item in res.get("items") or []:
        details = item.get("contentDetails") or {}
        snippet = item.get("snippet") or {}
        resource = snippet.get("resourceId") or {}
        vid = (
            (details.get("videoId") or "").strip()
            or (resource.get("videoId") or "").strip()
        )
        if not vid:
            continue
        title = (snippet.get("title") or "").strip()
        if title.lower() in {"deleted video", "private video"}:
            continue
        out.append(
            {
                "video_id": vid,
                "title": title,
                "channel_id": snippet.get("channelId") or channel_id,
                "channel_title": snippet.get("channelTitle") or "",
                "thumbnail_url": _thumbnail_from_snippet(snippet, vid),
                "published_at": details.get("videoPublishedAt")
                or snippet.get("publishedAt"),
                "live_broadcast_content": snippet.get("liveBroadcastContent") or "",
            }
        )
    return out


def _enrich_live_details(youtube, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach liveStreamingDetails (scheduled/actual start) via videos.list."""
    if not rows:
        return []
    ids = [r["video_id"] for r in rows]
    res = _call_with_network_retry(
        lambda: youtube.videos()
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
        actual_start = _parse_yt_time(live.get("actualStartTime"))
        scheduled_start = _parse_yt_time(live.get("scheduledStartTime"))
        actual_end = _parse_yt_time(live.get("actualEndTime"))
        scheduled_end = _parse_yt_time(live.get("scheduledEndTime"))
        starts = actual_start or scheduled_start
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
                "actual_start_at": actual_start,
                "actual_end_at": actual_end,
                "scheduled_end_at": scheduled_end,
                "ends_at": actual_end or scheduled_end,
                "live_broadcast_content": snippet.get("liveBroadcastContent")
                or row.get("live_broadcast_content")
                or "",
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


def _is_currently_live(row: Dict[str, Any], now: datetime) -> bool:
    """True only for an in-progress broadcast, not upcoming or ended."""
    actual_end = row.get("actual_end_at")
    if isinstance(actual_end, datetime) and actual_end <= now:
        return False
    live_bc = (row.get("live_broadcast_content") or "").strip().lower()
    return live_bc == "live"


def _effective_end_time(row: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """When the broadcast actually ended, ignoring a future scheduledEndTime."""
    actual_end = row.get("actual_end_at")
    if isinstance(actual_end, datetime):
        return actual_end
    live_bc = (row.get("live_broadcast_content") or "").strip().lower()
    if live_bc == "live" and _is_currently_live(row, now):
        return None
    scheduled_end = row.get("scheduled_end_at") or row.get("ends_at")
    if isinstance(scheduled_end, datetime) and scheduled_end <= now:
        return scheduled_end
    published = _parse_yt_time(row.get("published_at"))
    starts = row.get("starts_at")
    if live_bc == "none":
        return published or (starts if isinstance(starts, datetime) else None)
    return published


def _was_livestream(row: Dict[str, Any]) -> bool:
    return isinstance(row.get("actual_start_at"), datetime) or isinstance(
        row.get("actual_end_at"), datetime
    )


def _load_rows_by_channel(
    youtube,
    config: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    rows_by_channel: Dict[str, List[Dict[str, Any]]] = {}
    for ch in config.get("channels") or []:
        channel_id = (ch or {}).get("id") or ""
        if not channel_id:
            continue
        hits = _list_channel_uploads(youtube, channel_id)
        rows_by_channel[channel_id] = _enrich_live_details(youtube, hits)
    return rows_by_channel


def _cached_rows_by_channel(
    youtube,
    config: Dict[str, Any],
    use_prod_cache: bool,
) -> Tuple[Dict[str, List[Dict[str, Any]]], bool]:
    """
    One uploads+videos pass for all configured channels.

    Returns (rows_by_channel, stale). On quota / transient failure, reuse the
    last good snapshot if we have one.
    """
    global _snapshot_cache
    if use_prod_cache:
        cached = _snapshot_cache.get("rows_by_channel")
        expires = float(_snapshot_cache.get("expires_at") or 0)
        if cached is not None and time.time() < expires:
            return cached, False
    try:
        rows = _load_rows_by_channel(youtube, config)
    except Exception as exc:
        cached = _snapshot_cache.get("rows_by_channel")
        if cached is not None and (
            is_youtube_quota_error(exc) or _is_transient_network_error(exc)
        ):
            print(
                f"YouTube live snapshot failed; serving stale cache: {redact_youtube_error(exc)}",
                flush=True,
            )
            return cached, True
        raise
    if use_prod_cache:
        _snapshot_cache = {
            "expires_at": time.time() + SNAPSHOT_TTL_SECONDS,
            "rows_by_channel": rows,
        }
    return rows, False


def _find_live_session(
    rows_by_channel: Dict[str, List[Dict[str, Any]]],
    config: Dict[str, Any],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    for ch in config.get("channels") or []:
        channel_id = (ch or {}).get("id") or ""
        if not channel_id:
            continue
        for row in rows_by_channel.get(channel_id) or []:
            if _is_currently_live(row, now):
                return _session_payload(
                    status="live",
                    source="youtube_live",
                    row=row,
                    config=config,
                    channel_meta=_channel_meta(config, channel_id),
                )
    return None


def _find_soonest_upcoming(
    rows_by_channel: Dict[str, List[Dict[str, Any]]],
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
        candidates = []
        for row in rows_by_channel.get(channel_id) or []:
            if _is_currently_live(row, now):
                continue
            starts = row.get("starts_at")
            if not isinstance(starts, datetime):
                continue
            live_bc = (row.get("live_broadcast_content") or "").strip().lower()
            if live_bc not in {"upcoming", ""}:
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


def _latest_completed_for_channel(
    rows: List[Dict[str, Any]],
    config: Dict[str, Any],
    channel_id: str,
    now: datetime,
    within_hours: int,
) -> Optional[Dict[str, Any]]:
    """Newest completed livestream for one channel ending within the window."""
    horizon_start = now - timedelta(hours=within_hours)
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        if _is_currently_live(row, now):
            continue
        if not _was_livestream(row):
            continue
        ends = _effective_end_time(row, now)
        if not isinstance(ends, datetime):
            continue
        if horizon_start <= ends <= now:
            row = dict(row)
            row["ends_at"] = ends
            candidates.append(row)
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: r["ends_at"]
        if isinstance(r.get("ends_at"), datetime)
        else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    best = candidates[0]
    return _recent_recording_payload(
        row=best,
        config=config,
        channel_meta=_channel_meta(config, channel_id),
    )


def _recent_recording_payload(
    *,
    row: Dict[str, Any],
    config: Dict[str, Any],
    channel_meta: Dict[str, str],
) -> Dict[str, Any]:
    starts = row.get("starts_at")
    ends = row.get("ends_at")
    published = _parse_yt_time(row.get("published_at"))
    video_id = row["video_id"]
    return {
        "id": f"ended_{video_id}",
        "status": "ended",
        "source": "youtube_completed",
        "video_id": video_id,
        "title": row.get("title") or "",
        "channel_id": channel_meta.get("id") or row.get("channel_id") or "",
        "channel_title": channel_meta.get("title")
        or row.get("channel_title")
        or "",
        "channel_handle": channel_meta.get("handle") or "",
        "starts_at": starts.isoformat() if isinstance(starts, datetime) else None,
        "ends_at": ends.isoformat() if isinstance(ends, datetime) else None,
        "published_at": published.isoformat() if isinstance(published, datetime) else None,
        "youtube_watch_url": _watch_url(video_id),
        "youtube_thumbnail_url": row.get("thumbnail_url") or "",
    }


def _with_snapshot(
    *,
    now: Optional[datetime],
    config_path: Optional[Path],
    youtube_client,
    use_cache: bool,
    build,
):
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    server_time = current.isoformat()
    config = _load_config(config_path)
    use_prod_cache = use_cache and now is None and youtube_client is None

    def _run():
        youtube = youtube_client if youtube_client is not None else _get_youtube()
        rows_by_channel, stale = _cached_rows_by_channel(
            youtube, config, use_prod_cache
        )
        payload = build(rows_by_channel, config, current, server_time)
        if stale:
            payload = dict(payload)
            payload["stale"] = True
        return payload

    if youtube_client is None:
        with youtube_io_lock:
            return _run()
    return _run()


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

    def _build(rows_by_channel, config, current, server_time):
        session = _find_live_session(rows_by_channel, config, current)
        if session is None:
            session = _find_soonest_upcoming(rows_by_channel, config, current)
        return {
            "server_time": server_time,
            "session": session,
        }

    return _with_snapshot(
        now=now,
        config_path=config_path,
        youtube_client=youtube_client,
        use_cache=use_cache,
        build=_build,
    )


def resolve_recent_recordings(
    now: Optional[datetime] = None,
    config_path: Optional[Path] = None,
    youtube_client=None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    At most one completed livestream per configured channel within N hours.

    Returns {"server_time", "within_hours", "items": [...]}.
    """

    def _build(rows_by_channel, config, current, server_time):
        within_hours = int(
            config.get("recent_within_hours") or DEFAULT_RECENT_WITHIN_HOURS
        )
        items: List[Dict[str, Any]] = []
        for ch in config.get("channels") or []:
            channel_id = (ch or {}).get("id") or ""
            if not channel_id:
                continue
            item = _latest_completed_for_channel(
                rows_by_channel.get(channel_id) or [],
                config,
                channel_id,
                current,
                within_hours,
            )
            if item is not None:
                items.append(item)
        return {
            "server_time": server_time,
            "within_hours": within_hours,
            "items": items,
        }

    return _with_snapshot(
        now=now,
        config_path=config_path,
        youtube_client=youtube_client,
        use_cache=use_cache,
        build=_build,
    )


# Back-compat alias used by earlier drafts / callers
resolve_live_sessions = resolve_next_session
