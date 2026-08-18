#!/usr/bin/env python3
"""Unit tests for dynamic live/upcoming session resolution."""

from __future__ import annotations

import json
import ssl
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.live_sessions import resolve_next_session, resolve_recent_recordings


def _cfg(path: Path) -> Path:
    data = {
        "zoom_meeting_url": "https://us06web.zoom.us/j/2121217171",
        "upcoming_within_hours": 72,
        "recent_within_hours": 72,
        "channels": [
            {
                "id": "UCchanA",
                "title": "Channel A",
                "handle": "@channelA",
            },
            {
                "id": "UCchanB",
                "title": "Channel B",
                "handle": "@channelB",
            },
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _FakeReq:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def _playlist_item(search_item):
    vid = search_item["id"]["videoId"]
    snippet = dict(search_item.get("snippet") or {})
    snippet["resourceId"] = {"kind": "youtube#video", "videoId": vid}
    return {
        "snippet": snippet,
        "contentDetails": {
            "videoId": vid,
            "videoPublishedAt": snippet.get("publishedAt"),
        },
    }


class FakeYouTube:
    """Minimal YouTube client stub for playlistItems + videos.list."""

    def __init__(
        self,
        live_by_channel=None,
        upcoming_by_channel=None,
        completed_by_channel=None,
        video_details=None,
    ):
        self.live_by_channel = live_by_channel or {}
        self.upcoming_by_channel = upcoming_by_channel or {}
        self.completed_by_channel = completed_by_channel or {}
        self.video_details = video_details or {}

    def search(self):
        raise AssertionError(
            "search.list is quota-expensive and must not be used for live/recent"
        )

    def playlistItems(self):
        parent = self

        class PlaylistItems:
            def list(self, **kwargs):
                playlist_id = kwargs["playlistId"]
                channel_id = (
                    "UC" + playlist_id[2:]
                    if playlist_id.startswith("UU")
                    else playlist_id
                )
                items = []
                seen = set()
                for bucket in (
                    parent.live_by_channel,
                    parent.upcoming_by_channel,
                    parent.completed_by_channel,
                ):
                    for search_item in bucket.get(channel_id, []):
                        vid = (search_item.get("id") or {}).get("videoId")
                        if not vid or vid in seen:
                            continue
                        seen.add(vid)
                        items.append(_playlist_item(search_item))
                max_results = int(kwargs.get("maxResults") or 15)
                return _FakeReq({"items": items[:max_results]})

        return PlaylistItems()

    def videos(self):
        parent = self

        class Videos:
            def list(self, **kwargs):
                ids = [i for i in kwargs["id"].split(",") if i]
                items = [parent.video_details[i] for i in ids if i in parent.video_details]
                return _FakeReq({"items": items})

        return Videos()


def _search_item(video_id, channel_id, title, channel_title, live_broadcast=""):
    return {
        "id": {"videoId": video_id},
        "snippet": {
            "title": title,
            "channelId": channel_id,
            "channelTitle": channel_title,
            "liveBroadcastContent": live_broadcast,
            "thumbnails": {
                "high": {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
            },
        },
    }


def _video_detail(
    video_id,
    title,
    channel_id,
    channel_title,
    scheduled=None,
    actual_start=None,
    actual_end=None,
    live_broadcast=None,
):
    live = {}
    if scheduled:
        live["scheduledStartTime"] = scheduled
    if actual_start:
        live["actualStartTime"] = actual_start
    if actual_end:
        live["actualEndTime"] = actual_end
    if live_broadcast is None:
        if actual_end:
            live_broadcast = "none"
        elif actual_start:
            live_broadcast = "live"
        else:
            live_broadcast = "upcoming"
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "channelId": channel_id,
            "channelTitle": channel_title,
            "liveBroadcastContent": live_broadcast,
            "thumbnails": {
                "high": {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
            },
        },
        "liveStreamingDetails": live,
    }


class LiveSessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = _cfg(Path(self.tmp.name) / "live_sessions.json")
        self.now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefers_live_over_upcoming(self):
        yt = FakeYouTube(
            live_by_channel={
                "UCchanA": [
                    _search_item("live1", "UCchanA", "Live Now", "Channel A")
                ]
            },
            upcoming_by_channel={
                "UCchanB": [
                    _search_item("up1", "UCchanB", "Later", "Channel B")
                ]
            },
            video_details={
                "live1": _video_detail(
                    "live1",
                    "Live Now",
                    "UCchanA",
                    "Channel A",
                    actual_start="2026-07-26T17:55:00Z",
                ),
                "up1": _video_detail(
                    "up1",
                    "Later",
                    "UCchanB",
                    "Channel B",
                    scheduled="2026-07-26T20:00:00Z",
                ),
            },
        )
        payload = resolve_next_session(
            now=self.now,
            config_path=self.config_path,
            youtube_client=yt,
            use_cache=False,
        )
        self.assertIsNotNone(payload["session"])
        self.assertEqual(payload["session"]["status"], "live")
        self.assertEqual(payload["session"]["video_id"], "live1")
        self.assertEqual(
            payload["session"]["zoom_meeting_url"],
            "https://us06web.zoom.us/j/2121217171",
        )
        self.assertIn("hqdefault.jpg", payload["session"]["youtube_thumbnail_url"])

    def test_picks_soonest_upcoming_within_72h(self):
        later = (self.now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sooner = (self.now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        too_far = (self.now + timedelta(hours=80)).strftime("%Y-%m-%dT%H:%M:%SZ")
        yt = FakeYouTube(
            live_by_channel={},
            upcoming_by_channel={
                "UCchanA": [
                    _search_item("far", "UCchanA", "Far", "Channel A"),
                    _search_item("late", "UCchanA", "Late", "Channel A"),
                ],
                "UCchanB": [
                    _search_item("soon", "UCchanB", "Soon", "Channel B"),
                ],
            },
            video_details={
                "far": _video_detail(
                    "far", "Far", "UCchanA", "Channel A", scheduled=too_far
                ),
                "late": _video_detail(
                    "late", "Late", "UCchanA", "Channel A", scheduled=later
                ),
                "soon": _video_detail(
                    "soon", "Soon", "UCchanB", "Channel B", scheduled=sooner
                ),
            },
        )
        payload = resolve_next_session(
            now=self.now,
            config_path=self.config_path,
            youtube_client=yt,
            use_cache=False,
        )
        self.assertEqual(payload["session"]["video_id"], "soon")
        self.assertEqual(payload["session"]["status"], "upcoming")
        self.assertEqual(payload["session"]["channel_title"], "Channel B")

    def test_returns_null_when_nothing(self):
        yt = FakeYouTube()
        payload = resolve_next_session(
            now=self.now,
            config_path=self.config_path,
            youtube_client=yt,
            use_cache=False,
        )
        self.assertIsNone(payload["session"])

    def test_ended_stream_still_indexed_live_is_recent(self):
        """Ended broadcasts can still look live in snippets until videos.list."""
        yt = FakeYouTube(
            live_by_channel={
                "UCchanA": [
                    _search_item(
                        "ended1",
                        "UCchanA",
                        "Olga footsoak",
                        "Channel A",
                        live_broadcast="live",
                    )
                ]
            },
            completed_by_channel={},
            video_details={
                "ended1": _video_detail(
                    "ended1",
                    "Olga footsoak",
                    "UCchanA",
                    "Channel A",
                    actual_start="2026-07-26T16:00:00Z",
                    actual_end="2026-07-26T17:30:00Z",
                    live_broadcast="live",
                ),
            },
        )
        live_payload = resolve_next_session(
            now=self.now,
            config_path=self.config_path,
            youtube_client=yt,
            use_cache=False,
        )
        self.assertIsNone(live_payload["session"])

        recent = resolve_recent_recordings(
            now=self.now,
            config_path=self.config_path,
            youtube_client=yt,
            use_cache=False,
        )
        self.assertEqual(len(recent["items"]), 1)
        self.assertEqual(recent["items"][0]["video_id"], "ended1")
        self.assertEqual(recent["items"][0]["status"], "ended")

    def test_plain_upload_is_not_a_recent_session(self):
        yt = FakeYouTube(
            live_by_channel={
                "UCchanA": [_search_item("vod1", "UCchanA", "Talk", "Channel A")]
            },
            video_details={
                "vod1": _video_detail(
                    "vod1",
                    "Talk",
                    "UCchanA",
                    "Channel A",
                    live_broadcast="none",
                ),
            },
        )
        recent = resolve_recent_recordings(
            now=self.now,
            config_path=self.config_path,
            youtube_client=yt,
            use_cache=False,
        )
        self.assertEqual(recent["items"], [])


class TransientYoutubeErrorTests(unittest.TestCase):
    def test_timeout_and_ssl_are_retryable(self):
        from api.live_sessions import is_transient_youtube_error

        self.assertTrue(
            is_transient_youtube_error(TimeoutError("The read operation timed out"))
        )
        self.assertTrue(is_transient_youtube_error(ssl.SSLError("record layer failure")))
        ssl_msg = Exception(
            "[SSL: RECORD_LAYER_FAILURE] record layer failure (_ssl.c:2713)"
        )
        self.assertTrue(is_transient_youtube_error(ssl_msg))
        self.assertFalse(is_transient_youtube_error(ValueError("bad playlist")))

    def test_daily_quota_is_not_retryable_and_key_is_redacted(self):
        from api.live_sessions import (
            is_transient_youtube_error,
            is_youtube_quota_error,
            redact_youtube_error,
        )

        fake_key = "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAaaa"
        exc = Exception(
            "HttpError 429 when requesting "
            f"https://youtube.googleapis.com/youtube/v3/search?key={fake_key}&alt=json "
            'returned "Quota exceeded for quota metric \'Search Queries\' '
            "and limit 'Search Queries per day'\"."
        )
        self.assertTrue(is_youtube_quota_error(exc))
        self.assertFalse(is_transient_youtube_error(exc))
        redacted = redact_youtube_error(exc)
        self.assertNotIn(fake_key, redacted)
        self.assertIn("REDACTED", redacted)


if __name__ == "__main__":
    unittest.main()
