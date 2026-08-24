"""Unit tests for year playlist → session slicing."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.year_recordings import resolve_year_recordings


def _item(video_id, title, hours_ago, channel="UCx"):
    published = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "contentDetails": {"videoId": video_id, "videoPublishedAt": published},
        "snippet": {
            "title": title,
            "channelId": channel,
            "thumbnails": {},
            "publishedAt": published,
        },
    }


class FakePlaylistItems:
    def __init__(self, items):
        self._items = items

    def list(self, **kwargs):
        parent = self

        class Req:
            def execute(self_inner):
                return {"items": parent._items}

        return Req()


class FakeVideos:
    def __init__(self, live_by_id=None):
        # video_id -> liveBroadcastContent (default "none" = past / VOD)
        self._live_by_id = live_by_id or {}

    def list(self, **kwargs):
        parent = self
        ids = [i for i in (kwargs.get("id") or "").split(",") if i]

        class Req:
            def execute(self_inner):
                items = []
                for vid in ids:
                    live_bc = parent._live_by_id.get(vid, "none")
                    item = {
                        "id": vid,
                        "snippet": {
                            "title": vid,
                            "liveBroadcastContent": live_bc,
                        },
                    }
                    if live_bc == "upcoming":
                        item["liveStreamingDetails"] = {
                            "scheduledStartTime": "2099-01-01T00:00:00Z",
                        }
                    elif live_bc == "none" and vid.startswith("live_done"):
                        item["liveStreamingDetails"] = {
                            "actualStartTime": "2026-01-01T00:00:00Z",
                            "actualEndTime": "2026-01-01T01:00:00Z",
                        }
                    items.append(item)
                return {"items": items}

        return Req()


class FakeYouTube:
    def __init__(self, items, live_by_id=None):
        self._items = items
        self._live_by_id = live_by_id or {}

    def playlistItems(self):
        return FakePlaylistItems(self._items)

    def videos(self):
        return FakeVideos(self._live_by_id)


class YearRecordingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(
            {
                "years": [
                    {
                        "year": 2026,
                        "title": "21 Days 2026",
                        "playlist_id": "PLtest",
                        "sessions": [
                            {
                                "id": "s1",
                                "label": "Session 1",
                                "video_count": 2,
                                "start_date": "2026-01-05",
                                "end_date": "2026-01-25",
                            },
                            {
                                "id": "s2a",
                                "label": "Session 2A",
                                "video_count": 1,
                                "start_date": "2026-02-01",
                                "end_date": "2026-02-11",
                            },
                            {
                                "id": "s2b",
                                "label": "Session 2B",
                                "video_count": 1,
                                "start_date": "2026-02-12",
                                "end_date": "2026-02-21",
                            },
                        ],
                    }
                ]
            },
            self.tmp,
        )
        self.tmp.close()
        self.config_path = Path(self.tmp.name)

    def test_slices_oldest_first_by_session_counts(self):
        # hours_ago: larger = older
        items = [
            _item("new", "Newest", 1),
            _item("old", "Oldest", 40),
            _item("mid", "Middle", 20),
            _item("mid2", "Middle2", 10),
        ]
        payload = resolve_year_recordings(
            config_path=self.config_path,
            youtube_client=FakeYouTube(items),
            use_cache=False,
        )
        self.assertEqual(payload["year"], 2026)
        s1 = payload["sessions"][0]["videos"]
        self.assertEqual([v["video_id"] for v in s1], ["old", "mid"])
        self.assertEqual(
            [v["video_id"] for v in payload["sessions"][1]["videos"]],
            ["mid2"],
        )
        self.assertEqual(
            [v["video_id"] for v in payload["sessions"][2]["videos"]],
            ["new"],
        )
        self.assertEqual(payload["sessions"][0]["starts_at"], "2026-01-05")
        self.assertEqual(payload["sessions"][0]["ends_at"], "2026-01-25")
        self.assertEqual(payload["sessions"][1]["starts_at"], "2026-02-01")
        self.assertEqual(payload["sessions"][1]["ends_at"], "2026-02-11")

    def test_short_playlist_does_not_invent_videos(self):
        items = [_item("only", "Only", 5)]
        payload = resolve_year_recordings(
            config_path=self.config_path,
            youtube_client=FakeYouTube(items),
            use_cache=False,
        )
        self.assertEqual(len(payload["sessions"][0]["videos"]), 1)
        self.assertEqual(payload["sessions"][1]["videos"], [])
        self.assertEqual(payload["sessions"][2]["videos"], [])
        # Dates come from config even when a session has no videos yet.
        self.assertEqual(payload["sessions"][1]["starts_at"], "2026-02-01")
        self.assertEqual(payload["sessions"][1]["ends_at"], "2026-02-11")

    def test_omits_upcoming_and_live_playlist_videos(self):
        items = [
            _item("old", "Oldest past", 40),
            _item("mid", "Middle past", 20),
            _item("sched", "Scheduled future", 1),
            _item("live_now", "Currently live", 2),
        ]
        payload = resolve_year_recordings(
            config_path=self.config_path,
            youtube_client=FakeYouTube(
                items,
                live_by_id={
                    "sched": "upcoming",
                    "live_now": "live",
                },
            ),
            use_cache=False,
        )
        all_ids = [
            v["video_id"]
            for s in payload["sessions"]
            for v in s["videos"]
        ]
        self.assertEqual(all_ids, ["old", "mid"])
        self.assertNotIn("sched", all_ids)
        self.assertNotIn("live_now", all_ids)


if __name__ == "__main__":
    unittest.main()
