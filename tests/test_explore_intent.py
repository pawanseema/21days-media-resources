#!/usr/bin/env python3
"""Unit tests for Explore intent classification and list-vs-search routing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from search.intent import (
    INTENT_LIST,
    INTENT_SEMANTIC,
    classify_intent,
    classify_intent_rules,
)
from search.explore import run_explore_query


class IntentRulesTests(unittest.TestCase):
    def test_list_all_handouts(self):
        decision = classify_intent_rules("List all the handouts")
        self.assertIsNotNone(decision)
        self.assertEqual(decision["intent"], INTENT_LIST)
        self.assertEqual(decision["source"], "rules")

    def test_show_all_videos(self):
        decision = classify_intent_rules("show all videos")
        self.assertEqual(decision["intent"], INTENT_LIST)

    def test_what_handouts_do_you_have(self):
        decision = classify_intent_rules("What handouts do you have?")
        self.assertEqual(decision["intent"], INTENT_LIST)

    def test_browse_resources(self):
        decision = classify_intent_rules("browse the resources")
        self.assertEqual(decision["intent"], INTENT_LIST)

    def test_topical_query_is_not_list(self):
        self.assertIsNone(classify_intent_rules("heart chakra meditation"))
        self.assertIsNone(classify_intent_rules("Agnya clearing"))
        self.assertIsNone(classify_intent_rules("beginner meditation handout"))

    def test_classify_intent_uses_rules_without_llm(self):
        decision = classify_intent(
            "list all handouts",
            mode="resources",
            use_llm=False,
        )
        self.assertEqual(decision["intent"], INTENT_LIST)
        self.assertEqual(decision["mode"], "resources")
        self.assertEqual(decision["source"], "rules")

    def test_classify_intent_defaults_to_semantic_without_llm(self):
        decision = classify_intent(
            "heart chakra meditation",
            mode="videos",
            use_llm=False,
        )
        self.assertEqual(decision["intent"], INTENT_SEMANTIC)
        self.assertEqual(decision["mode"], "videos")


class ExploreRoutingTests(unittest.TestCase):
    def test_list_catalog_resources_skips_semantic_search(self):
        listed = {
            "results": [
                {
                    "resource_id": "resource_001",
                    "title": "A Handout",
                    "description": "Desc",
                    "topic": "General",
                    "tags": [],
                    "download_url": "https://example.com/a.pdf",
                    "file_type": "pdf",
                    "created_at": "",
                    "confidence": 1.0,
                }
            ],
            "count": 1,
            "total": 1,
            "limit": 50,
            "offset": 0,
        }
        with patch("search.explore.list_resources", return_value=listed) as list_mock:
            with patch("search.explore.search_resources") as search_mock:
                payload = run_explore_query(
                    "List all the handouts",
                    mode="resources",
                    use_llm=False,
                )
        list_mock.assert_called_once()
        search_mock.assert_not_called()
        self.assertEqual(payload["intent"], INTENT_LIST)
        self.assertEqual(payload["mode"], "resources")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["title"], "A Handout")

    def test_semantic_resources_skips_list(self):
        with patch("search.explore.list_resources") as list_mock:
            with patch(
                "search.explore.search_resources",
                return_value=[{"title": "Chakra", "confidence": 0.9}],
            ) as search_mock:
                payload = run_explore_query(
                    "chakra overview",
                    mode="resources",
                    top_k=5,
                    use_llm=False,
                )
        search_mock.assert_called_once()
        list_mock.assert_not_called()
        self.assertEqual(payload["intent"], INTENT_SEMANTIC)
        self.assertEqual(len(payload["results"]), 1)

    def test_list_catalog_videos_uses_video_catalog(self):
        listed = {
            "results": [
                {
                    "video_title": "Day 1",
                    "section_title": "Full video",
                    "timestamp": "",
                    "video_id": "abc",
                    "confidence": 1.0,
                    "result_kind": "video",
                }
            ],
            "count": 1,
            "total": 1,
            "limit": 50,
            "offset": 0,
        }
        with patch(
            "search.explore.list_videos_catalog", return_value=listed
        ) as list_mock:
            with patch("search.explore.search_video_sections") as search_mock:
                payload = run_explore_query(
                    "show all videos",
                    mode="videos",
                    use_llm=False,
                )
        list_mock.assert_called_once()
        search_mock.assert_not_called()
        self.assertEqual(payload["intent"], INTENT_LIST)
        self.assertEqual(payload["mode"], "videos")

    def test_list_videos_catalog_dedupes_by_video_id(self):
        from search.video_search import list_videos_catalog

        fake = {
            "ids": ["a_video", "b_video", "a_video_dup"],
            "metadatas": [
                {
                    "type": "video_context",
                    "video_id": "aaa",
                    "video_title": "Older",
                    "section_summary": "A",
                    "video_url": "https://youtube.com/watch?v=aaa",
                    "published_at": "2026-01-01T00:00:00Z",
                },
                {
                    "type": "video_context",
                    "video_id": "bbb",
                    "video_title": "Newer",
                    "section_summary": "B",
                    "video_url": "https://youtube.com/watch?v=bbb",
                    "published_at": "2026-06-01T00:00:00Z",
                },
                {
                    "type": "video_context",
                    "video_id": "aaa",
                    "video_title": "Older dup",
                    "section_summary": "dup",
                    "video_url": "https://youtube.com/watch?v=aaa",
                    "published_at": "2026-01-01T00:00:00Z",
                },
            ],
        }
        with patch("search.video_search.chroma_get", return_value=fake):
            payload = list_videos_catalog(limit=50, offset=0)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["video_id"], "bbb")
        self.assertEqual(payload["results"][0]["result_kind"], "video")
        self.assertEqual(payload["results"][0]["section_title"], "Full video")
        self.assertEqual(payload["results"][1]["video_id"], "aaa")


class ListResourcesHelperTests(unittest.TestCase):
    def test_list_resources_paginates_and_sorts(self):
        from search.resource_search import list_resources

        fake = {
            "ids": ["resource_002", "resource_001"],
            "metadatas": [
                {
                    "resource_id": "resource_002",
                    "title": "Zebra Guide",
                    "description": "Z",
                    "topic": "Practice",
                    "tags": "a,b",
                    "download_url": "https://example.com/z.pdf",
                    "file_type": "pdf",
                    "created_at": "2026-01-02",
                },
                {
                    "resource_id": "resource_001",
                    "title": "Alpha Guide",
                    "description": "A",
                    "topic": "Basics",
                    "tags": ["x"],
                    "download_url": "https://example.com/a.pdf",
                    "file_type": "pdf",
                    "created_at": "2026-01-01",
                },
            ],
        }
        with patch("search.resource_search.chroma_get", return_value=fake):
            page = list_resources(limit=1, offset=0)
            rest = list_resources(limit=1, offset=1)

        self.assertEqual(page["total"], 2)
        self.assertEqual(page["count"], 1)
        self.assertEqual(page["results"][0]["title"], "Alpha Guide")
        self.assertEqual(page["results"][0]["tags"], ["x"])
        self.assertEqual(rest["results"][0]["title"], "Zebra Guide")
        self.assertEqual(rest["results"][0]["tags"], ["a", "b"])
        self.assertEqual(page["results"][0]["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
