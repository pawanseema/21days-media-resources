#!/usr/bin/env python3
"""Unit tests for recommend_daily_meditation."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from search.video_search import (  # noqa: E402
    _is_guided_meditation_title,
    _is_music_meditation_title,
    _stable_pool_index,
    recommend_daily_meditation,
)


class TitleHeuristicTests(unittest.TestCase):
    def test_music_meditation(self):
        self.assertTrue(
            _is_music_meditation_title(
                "Meditation with Live Santoor music by Kirill"
            )
        )
        self.assertFalse(_is_music_meditation_title("Guided meditation on heart"))
        self.assertFalse(_is_music_meditation_title("Intro music + Quotes"))

    def test_guided(self):
        self.assertTrue(
            _is_guided_meditation_title("Guided meditation on Central Channel by Angi")
        )
        self.assertFalse(_is_guided_meditation_title("Silent meditation by Mark"))


class StableIndexTests(unittest.TestCase):
    def test_deterministic_and_in_range(self):
        a = _stable_pool_index("device-a", 10)
        b = _stable_pool_index("device-a", 10)
        c = _stable_pool_index("device-b", 10)
        self.assertEqual(a, b)
        self.assertTrue(0 <= a < 10)
        self.assertTrue(0 <= c < 10)


class RecommendDailyMeditationTests(unittest.TestCase):
    @patch("search.video_search.chroma_get")
    def test_picks_music_and_sums_guided(self, mock_get):
        mock_get.return_value = {
            "ids": ["v1_1_ts", "v1_2_ts", "v1_3_ts"],
            "documents": ["", "", ""],
            "metadatas": [
                {
                    "type": "timestamp_section",
                    "video_id": "vid1",
                    "timestamp": "0:00",
                    "section_title": "Intro Music + Quote",
                    "section_summary": "",
                    "video_title": "Day 1",
                    "video_url": "https://www.youtube.com/watch?v=vid1",
                    "section_duration_seconds": 60,
                },
                {
                    "type": "timestamp_section",
                    "video_id": "vid1",
                    "timestamp": "10:00",
                    "section_title": "Meditation with Live Violin music by Sia",
                    "section_summary": "Music",
                    "video_title": "Day 1",
                    "video_url": "https://www.youtube.com/watch?v=vid1",
                    "chakra": "Heart",
                    "quote": "",
                    "hashtags": "",
                    "published_at": "2026-01-01T00:00:00Z",
                    "section_duration_seconds": 600,
                },
                {
                    "type": "timestamp_section",
                    "video_id": "vid1",
                    "timestamp": "20:00",
                    "section_title": "Guided meditation on heart by Angi",
                    "section_summary": "Guided",
                    "video_title": "Day 1",
                    "video_url": "https://www.youtube.com/watch?v=vid1",
                    "section_duration_seconds": 400,
                },
            ],
        }
        payload = recommend_daily_meditation("device-1", exclude=[])
        self.assertEqual(payload["count"], 1)
        result = payload["result"]
        self.assertEqual(result["video_id"], "vid1")
        self.assertEqual(result["timestamp"], "10:00")
        self.assertEqual(result["section_duration_seconds"], 600)
        self.assertEqual(
            result["next_guided_section_title"],
            "Guided meditation on heart by Angi",
        )
        self.assertEqual(result["next_guided_duration_seconds"], 400)
        self.assertEqual(result["practice_duration_seconds"], 1000)

    @patch("search.video_search.chroma_get")
    def test_exclude_removes_candidate(self, mock_get):
        mock_get.return_value = {
            "ids": ["a_ts", "b_ts"],
            "documents": ["", ""],
            "metadatas": [
                {
                    "type": "timestamp_section",
                    "video_id": "vidA",
                    "timestamp": "5:00",
                    "section_title": "Meditation with Live Santoor music by Kirill",
                    "section_summary": "",
                    "video_title": "A",
                    "video_url": "https://www.youtube.com/watch?v=vidA",
                    "section_duration_seconds": 100,
                },
                {
                    "type": "timestamp_section",
                    "video_id": "vidB",
                    "timestamp": "6:00",
                    "section_title": "Meditation with Live Flute music by X",
                    "section_summary": "",
                    "video_title": "B",
                    "video_url": "https://www.youtube.com/watch?v=vidB",
                    "section_duration_seconds": 120,
                },
            ],
        }
        payload = recommend_daily_meditation(
            "device-1",
            exclude=[{"video_id": "vidA", "timestamp": "5:00"}],
        )
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["result"]["video_id"], "vidB")

    @patch("search.video_search.chroma_get")
    def test_empty_pool(self, mock_get):
        mock_get.return_value = {"ids": [], "documents": [], "metadatas": []}
        payload = recommend_daily_meditation("device-1", exclude=[])
        self.assertEqual(payload["count"], 0)
        self.assertIsNone(payload["result"])


if __name__ == "__main__":
    unittest.main()
