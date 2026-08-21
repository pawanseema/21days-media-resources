"""
Query intent classification for Explore (videos / handouts).

Closed intents only. Rules run first; optional LLM enum when rules miss.
Unknown / low confidence → semantic_search.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, Optional

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

INTENT_SEMANTIC = "semantic_search"
INTENT_LIST = "list_catalog"
VALID_INTENTS = frozenset({INTENT_SEMANTIC, INTENT_LIST})
VALID_MODES = frozenset({"videos", "resources"})

# Phrase / regex rules → list_catalog (corpus still scoped by mode).
_LIST_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\blist\s+all\b",
        r"\bshow\s+all\b",
        r"\bbrowse\s+all\b",
        r"\ball\s+the\s+(handouts?|resources?|videos?|clips?|sections?)\b",
        r"\ball\s+(handouts?|resources?|videos?|clips?)\b",
        r"\bevery\s+(handout|resource|video|clip)\b",
        r"\bwhat\s+(handouts?|resources?|videos?)\s+(do\s+you\s+have|are\s+there|are\s+available)\b",
        r"\b(handouts?|resources?|videos?)\s+do\s+you\s+have\b",
        r"\blist\s+(the\s+)?(handouts?|resources?|videos?|clips?)\b",
        r"\bshow\s+(me\s+)?(the\s+)?(handouts?|resources?|videos?)\b",
        r"\bbrowse\s+(the\s+)?(handouts?|resources?|videos?)\b",
        r"\bcatalog\s+of\s+(handouts?|resources?|videos?)\b",
    )
]

_LLM_CONFIDENCE_FLOOR = 0.6


def _normalize_mode(mode: Optional[str]) -> str:
    raw = (mode or "videos").strip().lower()
    if raw in ("resource", "resources", "handout", "handouts"):
        return "resources"
    if raw in ("video", "videos", "clip", "clips"):
        return "videos"
    return "videos" if raw not in VALID_MODES else raw


def classify_intent_rules(query: str) -> Optional[Dict[str, Any]]:
    """
    Return a list_catalog decision when rules match; otherwise None.
    """
    text = (query or "").strip()
    if not text:
        return None
    for pattern in _LIST_PATTERNS:
        if pattern.search(text):
            return {
                "intent": INTENT_LIST,
                "confidence": 1.0,
                "source": "rules",
            }
    return None


def classify_intent_llm(query: str, mode: str, openai_client=None) -> Dict[str, Any]:
    """
    Closed-enum LLM classifier. Falls back to semantic_search on any failure.
    """
    if openai_client is None:
        try:
            from openai import OpenAI
            from config import load_openai_api_key

            key = load_openai_api_key()
            openai_client = OpenAI(api_key=key) if key else None
        except Exception:
            openai_client = None

    if openai_client is None:
        return {
            "intent": INTENT_SEMANTIC,
            "confidence": 0.0,
            "source": "llm_unavailable",
        }

    system = (
        "You classify Explore search queries for a Sahaja Yoga meditation app. "
        "Return ONLY compact JSON: "
        '{"intent":"semantic_search"|"list_catalog","confidence":0.0-1.0}. '
        "Use list_catalog only when the user clearly wants to browse or list the "
        "entire catalog (all handouts/videos), not when they seek a specific topic. "
        "Use semantic_search for topical discovery, questions, and ambiguous asks. "
        f"Active mode is {mode} (corpus only; does not change intent labels)."
    )
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError(f"No JSON object in LLM response: {raw[:120]!r}")
        data = json.loads(match.group(0))
        intent = str(data.get("intent") or "").strip()
        confidence = float(data.get("confidence") or 0.0)
        if intent not in VALID_INTENTS:
            intent = INTENT_SEMANTIC
            confidence = 0.0
        if intent == INTENT_LIST and confidence < _LLM_CONFIDENCE_FLOOR:
            return {
                "intent": INTENT_SEMANTIC,
                "confidence": confidence,
                "source": "llm_low_confidence",
            }
        return {
            "intent": intent,
            "confidence": max(0.0, min(1.0, confidence)),
            "source": "llm",
        }
    except Exception as exc:
        print(f"intent LLM classify failed: {exc}", flush=True)
        return {
            "intent": INTENT_SEMANTIC,
            "confidence": 0.0,
            "source": "llm_error",
        }


def classify_intent(
    query: str,
    mode: str = "videos",
    *,
    use_llm: bool = True,
    openai_client=None,
) -> Dict[str, Any]:
    """
    Classify Explore query intent.

    Returns {intent, confidence, source, mode}.
    """
    normalized_mode = _normalize_mode(mode)
    text = (query or "").strip()
    if not text:
        return {
            "intent": INTENT_SEMANTIC,
            "confidence": 0.0,
            "source": "empty",
            "mode": normalized_mode,
        }

    ruled = classify_intent_rules(text)
    if ruled is not None:
        return {**ruled, "mode": normalized_mode}

    if use_llm:
        llm = classify_intent_llm(text, normalized_mode, openai_client=openai_client)
        return {**llm, "mode": normalized_mode}

    return {
        "intent": INTENT_SEMANTIC,
        "confidence": 0.0,
        "source": "default",
        "mode": normalized_mode,
    }
