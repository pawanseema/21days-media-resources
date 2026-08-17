#!/usr/bin/env python3
"""Unit tests for list_video_chapters (Chroma timestamp sections)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from search.video_search import (  # noqa: E402
    _timestamp_to_seconds,
    list_video_chapters,
)


class TimestampToSecondsTests(unittest.TestCase):
    def test_mm_ss(self):
        self.assertEqual(_timestamp_to_seconds("4:12"), 252)

    def test_h_mm_ss(self):
        self.assertEqual(_timestamp_to_seconds("1:02:03"), 3723)


class ListVideoChaptersTests(unittest.TestCase):
    def test_empty_video_id_raises(self):
        with self.assertRaises(ValueError):
            list_video_chapters("  ", use_cache=False)

    @patch("search.video_search.chroma_get")
    def test_sorts_and_maps_chapters(self, mock_get):
        mock_get.return_value = {
            "metadatas": [
                {"timestamp": "12:30", "section_title": "Meditation"},
                {"timestamp": "0:00", "section_title": "Introduction"},
                {"timestamp": "1:00:00", "section_title": "Closing"},
            ]
        }
        payload = list_video_chapters("abc123", use_cache=False)
        self.assertEqual(payload["video_id"], "abc123")
        titles = [c["section_title"] for c in payload["chapters"]]
        self.assertEqual(titles, ["Introduction", "Meditation", "Closing"])
        self.assertEqual(payload["chapters"][0]["start_seconds"], 0)
        self.assertEqual(payload["chapters"][1]["start_seconds"], 750)

    @patch("search.video_search.chroma_get")
    def test_skips_intro_music_bumper(self, mock_get):
        mock_get.return_value = {
            "metadatas": [
                {"timestamp": "0:00", "section_title": "Intro Music + Quote"},
                {"timestamp": "0:45", "section_title": "Welcome"},
            ]
        }
        payload = list_video_chapters("abc123", use_cache=False)
        self.assertEqual(
            [c["section_title"] for c in payload["chapters"]],
            ["Welcome"],
        )

    @patch("search.video_search.chroma_get")
    def test_missing_ingestion_returns_empty(self, mock_get):
        mock_get.return_value = {"metadatas": []}
        payload = list_video_chapters("unknown", use_cache=False)
        self.assertEqual(payload["chapters"], [])


if __name__ == "__main__":
    unittest.main()
