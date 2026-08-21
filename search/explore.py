"""
Explore query orchestration: classify intent, then search or list catalog.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from search.intent import (  # noqa: E402
    INTENT_LIST,
    INTENT_SEMANTIC,
    classify_intent,
)
from search.resource_search import (  # noqa: E402
    DEFAULT_LIST_PAGE_SIZE as RESOURCE_PAGE_SIZE,
    list_resources,
    search_resources,
)
from search.video_search import (  # noqa: E402
    DEFAULT_LIST_PAGE_SIZE as VIDEO_PAGE_SIZE,
    list_videos_catalog,
    search_video_sections,
)


def run_explore_query(
    query: str,
    mode: str = "videos",
    *,
    top_k: int = 5,
    limit: Optional[int] = None,
    offset: int = 0,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Route an Explore query to semantic search or catalog browse.

    Returns {intent, confidence, source, mode, query, results, count, ...}.
    List responses also include total, limit, offset.
    """
    text = (query or "").strip()
    decision = classify_intent(text, mode, use_llm=use_llm)
    normalized_mode = decision["mode"]
    intent = decision["intent"]

    payload: Dict[str, Any] = {
        "intent": intent,
        "confidence": decision.get("confidence", 0.0),
        "source": decision.get("source", ""),
        "mode": normalized_mode,
        "query": text,
    }

    if intent == INTENT_LIST:
        page_size = limit
        if page_size is None:
            page_size = RESOURCE_PAGE_SIZE if normalized_mode == "resources" else VIDEO_PAGE_SIZE
        if normalized_mode == "resources":
            listed = list_resources(limit=page_size, offset=offset)
        else:
            listed = list_videos_catalog(limit=page_size, offset=offset)
        payload.update(listed)
        return payload

    # semantic_search (default)
    k = top_k if isinstance(top_k, int) and top_k >= 1 else 5
    if normalized_mode == "resources":
        results = search_resources(text, top_k=k)
    else:
        results = search_video_sections(text, top_k=k)
    payload["results"] = results
    payload["count"] = len(results)
    return payload
