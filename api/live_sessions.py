"""Live meditation session schedule for the 21Days mobile app."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "live_sessions.json"


def _load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = path or Path(
        os.environ.get("LIVE_SESSIONS_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    with config_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("live_sessions.json must be a JSON object")
    return data


def _parse_local_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid local_time: {value!r} (expected HH:MM)")
    return int(parts[0]), int(parts[1])


def _session_window(
    starts_at: datetime,
    duration_minutes: int,
    early_join_minutes: int,
    now: datetime,
) -> str:
    """Return status: live | upcoming | ended."""
    join_open = starts_at - timedelta(minutes=early_join_minutes)
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    if join_open <= now < ends_at:
        return "live"
    if now < join_open:
        return "upcoming"
    return "ended"


def resolve_next_session(
    now: Optional[datetime] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Resolve the current or next recurring live session from config.

    Returns a payload shaped for GET /api/live/sessions.
    """
    config = _load_config(config_path)
    tz_name = config.get("timezone") or "America/Los_Angeles"
    tz = ZoneInfo(tz_name)
    current = (now or datetime.now(timezone.utc)).astimezone(tz)

    hour, minute = _parse_local_time(config.get("local_time", "19:00"))
    duration_minutes = int(config.get("duration_minutes", 90))
    early_join_minutes = int(config.get("early_join_minutes", 5))

    start_local = current.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    # If today's session has already ended, roll to tomorrow.
    if current >= start_local + timedelta(minutes=duration_minutes):
        start_local = start_local + timedelta(days=1)

    status = _session_window(
        start_local, duration_minutes, early_join_minutes, current
    )
    ends_at = start_local + timedelta(minutes=duration_minutes)

    youtube = (config.get("youtube_live_url") or "").strip()
    zoom = (config.get("zoom_meeting_url") or "").strip()

    session = {
        "id": f"session_{start_local.date().isoformat()}",
        "title": config.get("title") or "Live Meditation",
        "description": config.get("description") or "",
        "status": status,
        "starts_at": start_local.isoformat(),
        "ends_at": ends_at.isoformat(),
        "timezone": tz_name,
        "duration_minutes": duration_minutes,
        "early_join_minutes": early_join_minutes,
        "youtube_live_url": youtube,
        "zoom_meeting_url": zoom,
    }

    return {
        "session": session,
        "server_time": current.isoformat(),
    }
