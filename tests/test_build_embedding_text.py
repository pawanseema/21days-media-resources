#!/usr/bin/env python3
"""Unit tests for build_embedding_text (section vs video chrome)."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from resources.video_processing import build_embedding_text


class BuildEmbeddingTextTests(unittest.TestCase):
    def _base_row(self, row_type: str) -> dict:
        return {
            "type": row_type,
            "video_title": "Day 44: The Power of the Left Channel",
            "published_at": "2026-05-22T12:00:00Z",
            "timestamp": "2:08",
            "section_title": "Introduction by the MC - Linda",
            "section_summary": "",
            "chakra": "Left Side",
            "quote": "Opening of the heart is very important for all of us.",
            "hashtags": "#ida, #violin",
        }

    def test_video_context_keeps_quote_and_hashtags(self):
        text = build_embedding_text(self._base_row("video_context"))
        self.assertIn("Quote:", text)
        self.assertIn("Opening of the heart", text)
        self.assertIn("Hashtags:", text)
        self.assertIn("#ida", text)
        self.assertIn("Chakra: Left Side", text)

    def test_timestamp_section_omits_quote_and_hashtags(self):
        text = build_embedding_text(self._base_row("timestamp_section"))
        self.assertNotIn("Quote:", text)
        self.assertNotIn("Opening of the heart", text)
        self.assertNotIn("Hashtags:", text)
        self.assertNotIn("#ida", text)
        self.assertIn("Chakra: Left Side", text)
        self.assertIn("Introduction by the MC - Linda", text)
        self.assertIn("Day 44: The Power of the Left Channel", text)


if __name__ == "__main__":
    unittest.main()
