"""
Static wisdom topics shared by the web app and the 21Days mobile client.

Edit config/wisdom_topics.json once; both UIs read GET /api/wisdom/topics.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "wisdom_topics.json"


def load_wisdom_topics(path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = Path(
        os.environ.get("WISDOM_TOPICS_CONFIG", str(path or DEFAULT_CONFIG_PATH))
    )
    with config_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("wisdom_topics.json must be a JSON object")
    topics = data.get("topics")
    if not isinstance(topics, list):
        raise ValueError("wisdom_topics.json must include a topics array")
    return data
