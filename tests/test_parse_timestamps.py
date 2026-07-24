#!/usr/bin/env python3
"""Unit tests for parse_timestamps (last-section hashtag handling)."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from resources.video_processing import clean_description, parse_timestamps


class ParseTimestampsTests(unittest.TestCase):
    def test_middle_sections_unchanged(self):
        desc = """
0:00 Introduction
Opening remarks.

12:30 Meditation
Guided practice on the heart.

1:00:00 Closing
Final thoughts for seekers.
#FREEmeditationcourse #SahajaYoga
"""
        ts = parse_timestamps(desc)
        self.assertEqual(len(ts), 3)
        self.assertEqual(ts[0]["section_title"], "Introduction")
        self.assertIn("Opening remarks", ts[0]["section_summary"])
        self.assertEqual(ts[1]["section_title"], "Meditation")
        self.assertIn("Guided practice", ts[1]["section_summary"])
        self.assertNotIn("#", ts[0]["section_summary"])
        self.assertNotIn("#", ts[1]["section_summary"])

    def test_last_section_drops_trailing_hashtags(self):
        desc = """
0:00 Introduction
Welcome.

1:01:40 Announcements and Presenter experience sharing
A Q&A segment discussing how childlike innocence protects us and the role of Mother Earth in grounding meditation.
#FREEmeditationcourse #SahajaYoga #choices #enlightened #kundaliniawakening #om #innocence
"""
        ts = parse_timestamps(desc)
        self.assertEqual(len(ts), 2)
        last = ts[-1]
        self.assertEqual(
            last["section_title"],
            "Announcements and Presenter experience sharing",
        )
        self.assertIn("childlike innocence", last["section_summary"])
        self.assertIn("Mother Earth", last["section_summary"])
        self.assertNotIn("#", last["section_summary"])
        self.assertNotIn("FREEmeditationcourse", last["section_summary"])

    def test_last_section_hashtags_only_yields_empty_summary(self):
        desc = """
0:00 Intro
Hello.

5:00 Announcements
#FREEmeditationcourse #SahajaYoga #kundalini
"""
        ts = parse_timestamps(desc)
        self.assertEqual(len(ts), 2)
        self.assertEqual(ts[-1]["section_title"], "Announcements")
        self.assertEqual(ts[-1]["section_summary"], "")

    def test_inline_hashtags_stripped_from_prose_line(self):
        desc = """
0:00 Talk
A short teaching about innocence. #innocence #purity
"""
        ts = parse_timestamps(desc)
        self.assertEqual(len(ts), 1)
        self.assertIn("innocence", ts[0]["section_summary"])
        self.assertNotIn("#", ts[0]["section_summary"])
        self.assertNotIn("purity", ts[0]["section_summary"])  # was only a tag

    def test_clean_description_then_parse_drops_access_recording_and_hashtags(self):
        """Mirrors real ingest: clean_description first, then parse_timestamps."""
        raw = """
Day overview for seekers.

0:00 Introduction
Opening.

1:01:40 Announcements and Presenter experience sharing
A Q&A segment discussing how childlike innocence protects us.
Access recording of all previous sessions - https://www.youtube.com/playlist?list=PL2yoRtZOeeuHt7ZbJ1DCK4fDstdy6CxII
#FREEmeditationcourse #SahajaYoga #choices
"""
        cleaned = clean_description(raw)
        ts = parse_timestamps(cleaned)
        self.assertEqual(len(ts), 2)
        last = ts[-1]
        self.assertIn("childlike innocence", last["section_summary"])
        self.assertNotIn("#", last["section_summary"])
        self.assertNotIn("Access recording", last["section_summary"])
        self.assertNotIn("youtube.com", last["section_summary"])

    def test_clean_description_day_filter_only_uppercase_line_start(self):
        raw = """
Overview paragraph.

0:00 Introduction
Practice during the day and rest in silence.

5:00 Guidance
Day by day the attention becomes quieter.

10:00 Schedule note
DAY 45 starts at 8pm ET

15:00 Closing
Final thoughts.
"""
        cleaned = clean_description(raw)
        self.assertIn("during the day", cleaned)
        self.assertIn("Day by day", cleaned)
        self.assertNotIn("DAY 45 starts", cleaned)
        ts = parse_timestamps(cleaned)
        by_title = {s["section_title"]: s["section_summary"] for s in ts}
        self.assertIn("during the day", by_title["Introduction"])
        self.assertIn("Day by day", by_title["Guidance"])
        self.assertEqual(by_title["Schedule note"], "")  # DAY line dropped; no other body


if __name__ == "__main__":
    unittest.main()
