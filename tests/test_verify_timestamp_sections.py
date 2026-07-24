#!/usr/bin/env python3
"""Unit tests for timestamp-section YouTube vs Chroma verification helpers."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from resources.verify_timestamp_sections import (
    SectionSnapshot,
    compare_sections,
    normalize_text,
    normalize_timestamp,
)


class NormalizeTests(unittest.TestCase):
    def test_timestamp(self):
        self.assertEqual(normalize_timestamp("4:04"), "4:04")
        self.assertEqual(normalize_timestamp("04:04"), "4:04")
        self.assertEqual(normalize_timestamp("1:02:03"), "1:02:03")

    def test_text(self):
        self.assertEqual(normalize_text("  a   b\n"), "a b")
        self.assertEqual(normalize_text(None), "")


class CompareSectionsTests(unittest.TestCase):
    def test_ok_when_matching(self):
        yt = [
            SectionSnapshot("0:00", "Intro", ""),
            SectionSnapshot("4:04", "Welcome", "Hello there."),
        ]
        ch = [
            SectionSnapshot("0:00", "Intro", ""),
            SectionSnapshot("4:04", "Welcome", "Hello there."),
        ]
        self.assertEqual(compare_sections(yt, ch), [])

    def test_summary_missing_in_chroma(self):
        yt = [SectionSnapshot("14:10", "Meditation and Foot Soak", "A meditation session includes…")]
        ch = [SectionSnapshot("14:10", "Meditation and Foot Soak", "")]
        mismatches = compare_sections(yt, ch)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].kind, "summary_missing_in_chroma")
        self.assertTrue(mismatches[0].youtube_summary)
        self.assertEqual(mismatches[0].chroma_summary, "")

    def test_missing_sides_and_title(self):
        yt = [
            SectionSnapshot("0:00", "Intro", ""),
            SectionSnapshot("5:00", "Talk", "Body"),
        ]
        ch = [
            SectionSnapshot("0:00", "Intro music", ""),
            SectionSnapshot("9:00", "Extra", "Only in chroma"),
        ]
        kinds = {m.kind for m in compare_sections(yt, ch)}
        self.assertIn("title_mismatch", kinds)
        self.assertIn("missing_in_chroma", kinds)
        self.assertIn("missing_on_youtube", kinds)


if __name__ == "__main__":
    unittest.main()
