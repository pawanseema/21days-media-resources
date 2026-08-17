import os
import sys
import chromadb
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
import json
import re
from typing import Any, List, Dict
from collections import defaultdict

# Add project root to Python path for config import
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import functions from postprocess module
from .postprocess import fused_rank_score, explain_ranking

# Import API key loading functions
from config import load_openai_api_key, get_chroma_dir

CHROMA_DIR = get_chroma_dir()
COLLECTION_NAME = "sahajayoga_21_days_videos"
RELATED_COLLECTION_NAME = "sahajayoga_21_days_videos_related"

# Vector retrieval: fetch this many neighbors per query embedding (original + enriched merged, capped below).
INITIAL_VECTOR_CANDIDATES = 60

# Section-level candidates sent to the reranker (not "videos"). Higher = better recall but more tokens/latency;
# partial ID lists are repaired via _repair_llm_ranked_ids. 60 caused frequent truncation before repair existed.
LLM_RERANK_POOL_MAX = 45

# Opening bumper present as the first timestamp on every course video; never useful as a search/related hit.
EXCLUDED_SECTION_TITLES = frozenset({
    "Intro music + Quotes",
    "Intro Music + Quote",
    "Intro music + Quote",
    "Intro Music + Quotes",
})

# Set VIDEO_SEARCH_DEBUG=1 to log candidate counts from dual retrieval merge.
VIDEO_SEARCH_DEBUG = os.environ.get("VIDEO_SEARCH_DEBUG", "").lower() in ("1", "true", "yes")

# Ensure ChromaDB directory exists
os.makedirs(CHROMA_DIR, exist_ok=True)

# -----------------------------
# API Key Loading
# -----------------------------
OPENAI_API_KEY = load_openai_api_key()
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# -----------------------------
# ChromaDB Client
# -----------------------------
client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = client.get_collection(name=COLLECTION_NAME)
_related_collection = None


def _get_related_collection():
    """Lazy get-or-create for More like this segment embeddings."""
    global _related_collection
    if _related_collection is None:
        _related_collection = client.get_or_create_collection(
            name=RELATED_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _related_collection


def _is_transient_chroma_error(exc: BaseException) -> bool:
    """True for flaky GCS FUSE / SQLite I/O failures worth retrying."""
    msg = str(exc).lower()
    needles = (
        "disk i/o",
        "disk i/o error",
        "code: 266",
        "database is locked",
        "input/output error",
        "i/o error",
        "unable to open database file",
        "errno 5",
    )
    return any(n in msg for n in needles)


@retry(
    retry=retry_if_exception(_is_transient_chroma_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    reraise=True,
)
def chroma_query(**kwargs):
    """collection.query with retries for transient disk I/O errors."""
    return collection.query(**kwargs)


@retry(
    retry=retry_if_exception(_is_transient_chroma_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    reraise=True,
)
def chroma_get(**kwargs):
    """collection.get with retries for transient disk I/O errors."""
    return collection.get(**kwargs)


@retry(
    retry=retry_if_exception(_is_transient_chroma_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    reraise=True,
)
def related_chroma_query(**kwargs):
    """Related-collection query with retries for transient disk I/O errors."""
    return _get_related_collection().query(**kwargs)


@retry(
    retry=retry_if_exception(_is_transient_chroma_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    reraise=True,
)
def related_chroma_get(**kwargs):
    """Related-collection get with retries for transient disk I/O errors."""
    return _get_related_collection().get(**kwargs)


def enrich_user_query_llm(user_query: str):
    """
    Enrich user query for better search results.
    Preserves date information and focuses on Sahaja Yoga topics.
    """
    system = """You are a search query optimizer for Sahaja Yoga meditation videos. 
Your task is to rewrite queries to be more search-optimized while PRESERVING all specific information like dates, video titles, or timestamps.
- Keep all dates, dates, and temporal information exactly as stated
- Focus on Sahaja Yoga meditation topics, chakras, vibrations, footsoak, guided meditations
- Do NOT refuse queries about future dates - these are valid video search queries
- Return only the optimized query, nothing else"""

    if not openai_client:
        print("⚠️ OpenAI client not initialized. Returning original query.")
        return user_query

    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_query}
        ]
    )

    return resp.choices[0].message.content.strip()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_embedding(text):
    """Get embedding from OpenAI API with retry/backoff."""
    if not openai_client:
        raise ValueError("OpenAI client not initialized. Please check your OpenAI API key.")
    response = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=text
    )
    return response.data[0].embedding

def embed_user_query(q):
    return get_embedding(q)

def _is_excluded_section(meta: Dict) -> bool:
    """True for sections that must never appear in search, related, or chapters."""
    title = str((meta or {}).get("section_title") or "").strip()
    if not title:
        return False
    if title in EXCLUDED_SECTION_TITLES:
        return True
    lower = title.casefold()
    if lower in {t.casefold() for t in EXCLUDED_SECTION_TITLES}:
        return True
    # Bumper variants that start the same way in descriptions
    return lower.startswith("intro music")


def _timestamp_section_where(extra: Dict = None) -> Dict:
    """
    Chroma where: playable timestamp sections, excluding known bumper titles.
    """
    clauses = [
        {"type": "timestamp_section"},
        {"section_title": {"$nin": list(EXCLUDED_SECTION_TITLES)}},
    ]
    if extra:
        clauses.append(extra)
    return {"$and": clauses} if len(clauses) > 1 else clauses[0]


def chroma_vector_search(query: str, n_results=12):
    """
    Vector search over playable timestamp sections only.

    Excludes video_context rows and the shared "Intro music + Quotes" bumper
    so they never enter dual retrieval or LLM rerank.
    """
    query_embedding = get_embedding(query)
    # Note: Chroma query() include does not support "ids" in many versions; dedupe uses metadata keys.
    return chroma_query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=_timestamp_section_where(),
        include=["documents", "metadatas", "distances"],
    )


def _dedupe_hit_key(chroma_id, meta: Dict) -> str:
    """Stable key for merging two Chroma query result sets."""
    if chroma_id is not None and str(chroma_id).strip() != "":
        return str(chroma_id)
    m = meta or {}
    return f"{m.get('video_id', '')}|{m.get('timestamp', '')}|{m.get('section_title', '')}"


def _rows_from_chroma_raw(raw: Dict) -> List[tuple]:
    """(dedupe_key, distance, document, metadata) in Chroma relevance order."""
    if not raw or not raw.get("documents") or not raw["documents"][0]:
        return []
    docs = raw["documents"][0]
    metas = raw["metadatas"][0]
    dists = raw["distances"][0]
    id_rows = raw.get("ids")
    id_list = id_rows[0] if id_rows and id_rows[0] else [None] * len(docs)
    rows = []
    for i in range(len(docs)):
        cid = id_list[i] if i < len(id_list) else None
        rows.append((_dedupe_hit_key(cid, metas[i]), dists[i], docs[i], metas[i]))
    return rows


def merge_chroma_enriched_first(raw_orig: Dict, raw_enr: Dict, max_merged: int) -> Dict:
    """
    Merge dual vector results: **enriched list first** (Chroma order), then original-only hits.
    Avoids sorting the union by min(distance), which lets a broad user query flood the pool with
    generic neighbors and bury enriched-specific matches. Same shape as collection.query().
    """
    enr_rows = _rows_from_chroma_raw(raw_enr)
    orig_rows = _rows_from_chroma_raw(raw_orig)
    seen: set = set()
    acc: List[tuple] = []
    for key, dist, doc, meta in enr_rows:
        if len(acc) >= max_merged:
            break
        if key not in seen:
            seen.add(key)
            acc.append((dist, doc, meta))
    for key, dist, doc, meta in orig_rows:
        if len(acc) >= max_merged:
            break
        if key not in seen:
            seen.add(key)
            acc.append((dist, doc, meta))
    if not acc:
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    docs, metas, dists = [], [], []
    for dist, doc, meta in acc:
        docs.append(doc)
        metas.append(meta)
        dists.append(dist)
    return {"documents": [docs], "metadatas": [metas], "distances": [dists]}


def _repair_llm_ranked_ids(ranked_ids: List, n_items: int, items: List[Dict]) -> List[int]:
    """Turn a partial / messy ID list into a full permutation of 0..n_items-1 (missing tail by distance)."""
    out: List[int] = []
    seen: set = set()
    for x in ranked_ids:
        if isinstance(x, int) and 0 <= x < n_items and x not in seen:
            seen.add(x)
            out.append(x)
    missing = [i for i in range(n_items) if i not in seen]
    missing.sort(key=lambda i: items[i]["distance"])
    return out + missing


def _parse_ranked_ids_from_response(response_text: str):
    """
    Parse ranked ID array from LLM response text.
    Supports raw JSON arrays and fenced/annotated outputs containing one array.
    """
    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, list):
            return parsed, "direct_json"
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: extract first bracketed integer list, even if wrapped in markdown fences.
    m = re.search(r"\[[^\]]+\]", response_text, flags=re.MULTILINE | re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                return parsed, "extracted_array"
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Last fallback: recover integers from free-form text/codeblock and let repair logic normalize.
    ints = [int(x) for x in re.findall(r"\b\d+\b", response_text)]
    if ints:
        return ints, "extracted_integers"

    raise ValueError("No valid ranked ID array found in reranker response")


def dual_vector_retrieval(user_query: str, enriched: str):
    """
    Run vector search on original and (if meaningfully different) enriched query; merge and cap.
    Returns (merged_chroma_dict, used_dual: bool).
    """
    n = INITIAL_VECTOR_CANDIDATES
    raw_orig = chroma_vector_search(user_query, n_results=n)
    use_dual = (
        enriched.strip().lower() != user_query.strip().lower()
        and len(enriched.strip()) >= 2
    )
    if use_dual:
        raw_enr = chroma_vector_search(enriched, n_results=n)
        merged = merge_chroma_enriched_first(raw_orig, raw_enr, max_merged=n)
        if VIDEO_SEARCH_DEBUG:
            no = len(raw_orig["documents"][0]) if raw_orig.get("documents") else 0
            ne = len(raw_enr["documents"][0]) if raw_enr.get("documents") else 0
            nm = len(merged["documents"][0]) if merged.get("documents") else 0
            print(f"🔍 dual retrieval: original={no} enriched={ne} merged={nm} (cap={n})")
        return merged, True
    if VIDEO_SEARCH_DEBUG:
        no = len(raw_orig["documents"][0]) if raw_orig.get("documents") else 0
        print(f"🔍 single retrieval: candidates={no} (enrichment unchanged or empty)")
    return raw_orig, False

def keyword_score(query: str, text: str) -> float:
    q_words = set(re.findall(r"\w+", query.lower()))
    t_words = set(re.findall(r"\w+", text.lower()))
    overlap = q_words.intersection(t_words)
    return len(overlap) / (len(q_words) + 1e-6)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def rerank_with_llm(original_query, chroma_results):
    """
    Re-rank search results using LLM based on relevance to query.
    
    Args:
        original_query: Original user query
        chroma_results: Results from ChromaDB vector search
    
    Returns:
        Re-ranked list of items
    """
    items = []
    for i in range(len(chroma_results["documents"][0])):
        items.append({
            "id": i,
            "distance": chroma_results["distances"][0][i],
            "text": chroma_results["documents"][0][i],
            "meta": chroma_results["metadatas"][0][i],
        })

    if not items:
        return []

    # Check if OpenAI client is available
    if not openai_client:
        print("⚠️ OpenAI client not initialized. Using merge order.")
        return items

    try:
        # Extract published_at dates for date-based ranking
        items_with_dates = []
        for item in items:
            published_at = item["meta"].get("published_at", "")
            items_with_dates.append({
                **item,
                "published_at": published_at
            })

        pool = min(LLM_RERANK_POOL_MAX, len(items_with_dates))
        head = items_with_dates[:pool]
        tail = items_with_dates[pool:]
        if not head:
            return []

        # Detect if query mentions a date
        date_patterns = [
            r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\s+\d{4}',
            r'\b\d{1,2}(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}',
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?\s+\d{4}',
            r'\b\d{4}-\d{2}-\d{2}',
            r'\b\d{1,2}/\d{1,2}/\d{4}',
        ]
        has_date_mention = any(re.search(pattern, original_query, re.IGNORECASE) for pattern in date_patterns)
        
        # LLM sees only `head` (renumbered ids). Tail stays in merge order after reranked head.
        simplified_items = []
        for new_id, item in enumerate(head):
            simplified_items.append({
                "id": new_id,
                "text": item["text"][:200] + "..." if len(item["text"]) > 200 else item["text"],
                "published_at": item.get("published_at", ""),
                "video_title": item["meta"].get("video_title", "")[:100],
            })
        
        if has_date_mention:
            # Query mentions a date - include date ranking rules
            user_prompt = f"""
User query: "{original_query}"

You have {len(simplified_items)} search results. Rank them from MOST relevant (rank 0) to LEAST relevant (rank {len(simplified_items)-1}).

RANKING RULES:
1. PRIMARY: Rank by semantic relevance (how well the text matches the query meaning)
2. SECONDARY (if query mentions a date): Prioritize results where published_at matches the mentioned date. If no exact match, prioritize closest date.

Each result has an "id" field (0 to {len(simplified_items)-1}). Return a JSON array of these IDs in rank order.

Example: If you have 3 results and want to rank them as [result 2, result 0, result 1], return: [2, 0, 1]

Results:
{json.dumps(simplified_items, indent=2)}

Return ONLY a valid JSON array of IDs, nothing else. The array must contain all IDs from 0 to {len(simplified_items)-1} exactly once.
"""
            system_content = "You are a search ranking assistant. Return ONLY a valid JSON array of result IDs in rank order. The array must contain all IDs from 0 to N-1 exactly once. Example: [2,0,1]"
        else:
            # No date mention - pure semantic ranking
            user_prompt = f"""
User query: "{original_query}"

You have {len(simplified_items)} search results. Rank them from MOST relevant (rank 0) to LEAST relevant (rank {len(simplified_items)-1}) based on semantic relevance.

Rank by how well each result's text semantically matches the meaning and intent of the user query.

Each result has an "id" field (0 to {len(simplified_items)-1}). Return a JSON array of these IDs in rank order.

Example: If you have 3 results and want to rank them as [result 2, result 0, result 1], return: [2, 0, 1]

Results:
{json.dumps(simplified_items, indent=2)}

Return ONLY a valid JSON array of IDs, nothing else. The array must contain all IDs from 0 to {len(simplified_items)-1} exactly once.
"""
            system_content = "You are a search ranking assistant. Return ONLY a valid JSON array of result IDs in rank order. The array must contain all IDs from 0 to N-1 exactly once. Example: [2,0,1]"

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt}
            ],
        )
        response_text = response.choices[0].message.content.strip()
        
        # Parse JSON (robust to fenced blocks / minor wrapper text)
        try:
            ranked_ids, parse_mode = _parse_ranked_ids_from_response(response_text)
            if isinstance(ranked_ids, list) and len(ranked_ids) > 0:
                repaired = _repair_llm_ranked_ids(ranked_ids, len(head), head)
                if VIDEO_SEARCH_DEBUG:
                    print(
                        f"🔁 rerank parse={parse_mode} "
                        f"raw_count={len(ranked_ids)} repaired_count={len(repaired)}"
                    )
                ordered_head = [head[i] for i in repaired]
                tail_sorted = sorted(tail, key=lambda x: x["distance"])
                return ordered_head + tail_sorted
            print("Warning: LLM returned empty or non-list ranking. Using merge order.")
        except (json.JSONDecodeError, ValueError, IndexError) as e:
            print(f"Warning: Failed to parse LLM reranking response: {e}. Using merge order.")
            print(f"Response was: {response_text[:200]}")

    except Exception as e:
        print(f"Warning: LLM reranking failed: {e}. Using merge order.")

    return items

def compute_confidence(distance, keyword_boost):
    base = max(0, 1 - distance)
    return round(base + (0.15 * keyword_boost), 3)

def _normalize_timestamp(ts: str) -> str:
    """Normalize timestamp strings for comparison (e.g. 4:04 vs 04:04)."""
    if ts is None:
        return ""
    text = str(ts).strip()
    if not text:
        return ""
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return text
    if len(nums) == 2:
        return f"{nums[0]}:{nums[1]:02d}"
    if len(nums) == 3:
        return f"{nums[0]}:{nums[1]:02d}:{nums[2]:02d}"
    return text


def _timestamp_to_seconds(ts: str) -> int:
    """Convert `M:SS` / `H:MM:SS` to seconds; 0 if unparseable."""
    text = str(ts or "").strip()
    if not text:
        return 0
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return 0


_CHAPTERS_CACHE_TTL_SECONDS = 180
_chapters_cache: Dict[str, Any] = {}


def list_video_chapters(video_id: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Chroma timestamp_section rows for one video, oldest first.

    Returns {"video_id", "chapters": [{"timestamp", "section_title", "start_seconds"}]}.
    Empty chapters when the video is not ingested.
    """
    import time

    vid = (video_id or "").strip()
    if not vid:
        raise ValueError("video_id is required")

    if use_cache:
        cached = _chapters_cache.get(vid)
        if cached is not None and time.time() < float(cached.get("expires_at") or 0):
            return dict(cached["payload"])

    got = chroma_get(
        where={
            "$and": [
                {"video_id": vid},
                {"type": "timestamp_section"},
            ]
        },
        include=["metadatas"],
    )

    chapters: List[Dict[str, Any]] = []
    for meta in got.get("metadatas") or []:
        meta = meta or {}
        if _is_excluded_section(meta):
            continue
        ts = str(meta.get("timestamp") or "").strip()
        title = str(meta.get("section_title") or "").strip()
        if not ts:
            continue
        chapters.append(
            {
                "timestamp": ts,
                "section_title": title or "Section",
                "start_seconds": _timestamp_to_seconds(ts),
            }
        )

    chapters.sort(key=lambda c: c["start_seconds"])

    payload = {"video_id": vid, "chapters": chapters}
    if use_cache:
        _chapters_cache[vid] = {
            "expires_at": time.time() + _CHAPTERS_CACHE_TTL_SECONDS,
            "payload": payload,
        }
    return payload


def _embedding_as_list(embedding):
    if embedding is None:
        return None
    if hasattr(embedding, "tolist"):
        return embedding.tolist()
    return list(embedding)


def _section_duration_seconds(meta: Dict):
    """Parse section_duration_seconds from Chroma meta; None if missing/invalid."""
    if not meta:
        return None
    raw = meta.get("section_duration_seconds")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _card_from_meta(meta: Dict, document: str, distance: float, chroma_id: str = None) -> Dict:
    """Build a search-card-shaped result from Chroma metadata."""
    confidence = compute_confidence(distance, 0.0)
    card = {
        "video_title": meta.get("video_title", ""),
        "timestamp": meta.get("timestamp", ""),
        "section_title": meta.get("section_title", ""),
        "summary": meta.get("section_summary", ""),
        "url": meta.get("video_url", ""),
        "chakra": meta.get("chakra", ""),
        "quote": meta.get("quote", ""),
        "hashtags": meta.get("hashtags", ""),
        "published_at": meta.get("published_at", ""),
        "video_id": meta.get("video_id", ""),
        "confidence": confidence,
    }
    duration = _section_duration_seconds(meta)
    if duration is not None:
        card["section_duration_seconds"] = duration
    if chroma_id:
        card["chroma_id"] = chroma_id
    return card


def _segment_focus_text(meta: Dict) -> str:
    """
    Build a lean query string for More like this: section title + summary only.

    Omits video title, date, chakra, quote, and hashtags so neighbors match
    segment meaning rather than shared video/course chrome.
    """
    meta = meta or {}
    title = str(meta.get("section_title") or "").strip()
    summary = str(meta.get("section_summary") or "").strip()
    parts = []
    if title:
        parts.append(f"Section: {title}")
    if summary:
        parts.append(f"Summary: {summary}")
    return "\n".join(parts)


def _related_seed_embedding(seed: Dict) -> list:
    """
    Embedding for More like this neighbor search.

    Prefer the precomputed vector in the related collection (same chroma id).
    Fall back to a query-time title+summary embed, then the main stored embedding.
    """
    chroma_id = seed.get("chroma_id")
    if chroma_id:
        try:
            got = related_chroma_get(ids=[chroma_id], include=["embeddings"])
            if got and got.get("ids"):
                emb = _embedding_as_list(
                    got["embeddings"][0] if got.get("embeddings") is not None else None
                )
                if emb is not None:
                    return emb
        except Exception as e:
            print(
                f"Warning: related-collection seed lookup failed ({e}); "
                "falling back to query-time embed",
                flush=True,
            )

    stored = seed.get("embedding")
    focus = _segment_focus_text(seed.get("meta") or {})
    if not focus:
        return stored

    try:
        return get_embedding(focus)
    except Exception as e:
        print(
            f"Warning: segment-focus embed failed ({e}); "
            "falling back to stored section embedding",
            flush=True,
        )
        return stored


def _hydrate_cards_from_main(neighbor_ids, distances_by_id: Dict) -> List[Dict]:
    """
    Build cards from main-collection metadata for related neighbor ids.

    Related collection is used for similarity only; display fields (including
    section_duration_seconds) come from the main collection.
    """
    if not neighbor_ids:
        return []
    got = chroma_get(ids=list(neighbor_ids), include=["metadatas", "documents"])
    if not got or not got.get("ids"):
        return []
    by_id = {}
    for i, rid in enumerate(got["ids"]):
        meta = (got["metadatas"][i] if got.get("metadatas") else {}) or {}
        doc = (got["documents"][i] if got.get("documents") else "") or ""
        dist = float(distances_by_id.get(rid, 1.0))
        by_id[rid] = _card_from_meta(meta, doc, dist, chroma_id=rid)
    # Preserve neighbor order from related query
    return [by_id[rid] for rid in neighbor_ids if rid in by_id]


def resolve_seed_section(video_id: str = None, timestamp: str = None, chroma_id: str = None) -> Dict:
    """
    Load a timestamp_section seed from Chroma by id or (video_id, timestamp).

    Returns dict with: chroma_id, meta, document, embedding (list).
    Raises ValueError if not found or invalid args.
    """
    if chroma_id and str(chroma_id).strip():
        got = chroma_get(
            ids=[str(chroma_id).strip()],
            include=["metadatas", "documents", "embeddings"],
        )
        if not got or not got.get("ids"):
            raise ValueError(f"Seed section not found for id: {chroma_id}")
        meta = got["metadatas"][0] or {}
        document = (got["documents"][0] if got.get("documents") else "") or ""
        embedding = _embedding_as_list(got["embeddings"][0] if got.get("embeddings") is not None else None)
        if embedding is None and document:
            embedding = get_embedding(document)
        if embedding is None:
            raise ValueError(f"Seed section has no embedding: {chroma_id}")
        return {
            "chroma_id": got["ids"][0],
            "meta": meta,
            "document": document,
            "embedding": embedding,
        }

    vid = (video_id or "").strip()
    ts = (timestamp or "").strip()
    if not vid or not ts:
        raise ValueError("Provide chroma id, or both video_id and timestamp")

    got = chroma_get(
        where={
            "$and": [
                {"video_id": vid},
                {"type": "timestamp_section"},
            ]
        },
        include=["metadatas", "documents", "embeddings"],
    )
    if not got or not got.get("ids"):
        raise ValueError(f"Seed section not found for video_id={vid} timestamp={ts}")

    target = _normalize_timestamp(ts)
    match_idx = None
    for i, meta in enumerate(got["metadatas"]):
        meta = meta or {}
        if _normalize_timestamp(meta.get("timestamp", "")) == target:
            match_idx = i
            break
        # Also accept exact string match
        if str(meta.get("timestamp", "")).strip() == ts:
            match_idx = i
            break

    if match_idx is None:
        raise ValueError(f"Seed section not found for video_id={vid} timestamp={ts}")

    meta = got["metadatas"][match_idx] or {}
    document = (got["documents"][match_idx] if got.get("documents") else "") or ""
    embedding = _embedding_as_list(
        got["embeddings"][match_idx] if got.get("embeddings") is not None else None
    )
    if embedding is None and document:
        embedding = get_embedding(document)
    if embedding is None:
        raise ValueError(f"Seed section has no embedding for video_id={vid} timestamp={ts}")

    return {
        "chroma_id": got["ids"][match_idx],
        "meta": meta,
        "document": document,
        "embedding": embedding,
    }


def recommend_related(
    section_embedding=None,
    top_k=5,
    *,
    video_id: str = None,
    timestamp: str = None,
    chroma_id: str = None,
    exclude_same_video: bool = True,
):
    """
    Find topic-similar timestamp sections near a seed segment.

    Preferred: pass video_id+timestamp or chroma_id. Neighbor search uses the
    precomputed title+summary embedding in the related collection (no OpenAI
    call when the seed exists there). Card metadata (including duration) is
    hydrated from the main collection. /search is unchanged.

    Legacy: pass section_embedding directly (used as-is against related collection).

    Returns {"seed": {...}, "results": [card, ...]} when resolved via id/timestamp,
    or a bare list of cards when only section_embedding is provided (legacy).
    """
    top_k = max(1, int(top_k or 5))
    seed = None
    embedding = section_embedding

    if chroma_id or (video_id and timestamp):
        seed = resolve_seed_section(
            video_id=video_id,
            timestamp=timestamp,
            chroma_id=chroma_id,
        )
        embedding = _related_seed_embedding(seed)

    if embedding is None:
        raise ValueError("section_embedding or seed identity is required")

    embedding = _embedding_as_list(embedding)
    seed_video_id = (seed["meta"].get("video_id") if seed else video_id) or ""
    seed_chroma_id = seed["chroma_id"] if seed else None

    # Fetch extra neighbors so filters still leave top_k
    n_fetch = min(max(top_k * 8 + 10, 25), 80)
    raw = related_chroma_query(
        query_embeddings=[embedding],
        n_results=n_fetch,
        where={"section_title": {"$nin": list(EXCLUDED_SECTION_TITLES)}},
        include=["metadatas", "distances"],
    )

    empty_payload = {"seed": None, "results": []}
    if seed:
        empty_payload["seed"] = {
            "video_id": seed["meta"].get("video_id", ""),
            "timestamp": seed["meta"].get("timestamp", ""),
            "section_title": seed["meta"].get("section_title", ""),
            "video_title": seed["meta"].get("video_title", ""),
            "chroma_id": seed["chroma_id"],
        }

    if not raw or not raw.get("ids") or not raw["ids"][0]:
        return empty_payload if seed else []

    neighbor_ids = []
    distances_by_id = {}
    for i, rid in enumerate(raw["ids"][0]):
        meta = (raw["metadatas"][0][i] if raw.get("metadatas") else {}) or {}
        dist = float(raw["distances"][0][i]) if raw.get("distances") else 1.0

        if seed_chroma_id and rid == seed_chroma_id:
            continue
        if meta.get("type") and meta.get("type") != "timestamp_section":
            continue
        if _is_excluded_section(meta):
            continue
        if exclude_same_video and seed_video_id and meta.get("video_id") == seed_video_id:
            continue

        neighbor_ids.append(rid)
        distances_by_id[rid] = dist
        if len(neighbor_ids) >= top_k:
            break

    results = _hydrate_cards_from_main(neighbor_ids, distances_by_id)
    results.sort(key=lambda x: x.get("confidence") or 0, reverse=True)

    if seed is None and section_embedding is not None:
        # Legacy callers expected a list
        return results[:top_k]

    return {
        "seed": empty_payload["seed"],
        "results": results[:top_k],
    }


def search_video_sections(user_query: str, top_k=3):
    # 1 | Enrich query
    enriched = enrich_user_query_llm(user_query)

    # 2 | Dual vector search (original + enriched), merge/dedupe, cap at INITIAL_VECTOR_CANDIDATES
    raw, _ = dual_vector_retrieval(user_query, enriched)

    # 3 | Re-rank with LLM
    reranked = rerank_with_llm(user_query, raw)

    # 4 | Build output with scoring
    out = []
    for item in reranked:
        meta = item["meta"]
        if _is_excluded_section(meta):
            continue
        keyword_boost = keyword_score(user_query, item["text"])
        confidence = compute_confidence(item["distance"], keyword_boost)

        out.append({
            "video_title": meta["video_title"],
            "timestamp": meta["timestamp"],
            "section_title": meta["section_title"],
            "summary": meta["section_summary"],
            "url": meta["video_url"],
            "chakra": meta["chakra"],
            "quote": meta["quote"],
            "hashtags": meta["hashtags"],
            "published_at": meta.get("published_at", ""),
            "video_id": meta.get("video_id", ""),
            "confidence": confidence,
        })
        duration = _section_duration_seconds(meta)
        if duration is not None:
            out[-1]["section_duration_seconds"] = duration

    # 5 | Highest confidence first, then top K
    out.sort(key=lambda x: x.get("confidence") or 0, reverse=True)
    return out[:top_k]

class ConversationMemory:
    def __init__(self):
        self.history = []

    def add(self, user_query, results):
        self.history.append({"query": user_query, "results": results})

    def summarize(self):
        text = "\n".join([f"Q: {h['query']}" for h in self.history[-5:]])
        return text

conv = ConversationMemory()

def conversational_search(user_query):
    context = conv.summarize()

    enriched = f"Previous context:\n{context}\n\nNew Query: {user_query}"
    results = search_video_sections(enriched)

    conv.add(user_query, results)
    return results

def debug_trace(query):
    enriched = enrich_user_query_llm(query)
    print("\nEnriched Query:", enriched)

    raw_orig = chroma_vector_search(query, n_results=INITIAL_VECTOR_CANDIDATES)
    raw_enr = chroma_vector_search(enriched, n_results=INITIAL_VECTOR_CANDIDATES)
    raw = merge_chroma_enriched_first(raw_orig, raw_enr, max_merged=INITIAL_VECTOR_CANDIDATES)
    print(
        f"\nVector candidates: original={len(raw_orig['documents'][0])} "
        f"enriched={len(raw_enr['documents'][0])} merged={len(raw['documents'][0])}"
    )

    for i, d in enumerate(raw["documents"][0]):
        print(f"\n--------- Candidate {i} ---------")
        print("Distance:", raw["distances"][0][i])
        print("Metadata:", raw["metadatas"][0][i])
        print("Text:", d)

def evaluate_search(queries, expected_video_ids):
    """
    Evaluate search accuracy by comparing results with expected video IDs.
    
    Args:
        queries: List of search queries
        expected_video_ids: List of expected video IDs/titles
    
    Returns:
        Accuracy score (0.0 to 1.0)
    """
    if not queries or not expected_video_ids:
        return 0.0
    
    if len(queries) != len(expected_video_ids):
        raise ValueError("queries and expected_video_ids must have the same length")
    
    correct = 0
    total = len(queries)

    for q, expected in zip(queries, expected_video_ids):
        try:
            results = search_video_sections(q)
            if results and len(results) > 0:
                top_vid = results[0]["video_title"]
                if expected.lower() in top_vid.lower():
                    correct += 1
        except Exception as e:
            print(f"Warning: Search evaluation failed for query '{q}': {e}")
            # Continue with next query

    accuracy = correct / total if total > 0 else 0.0
    return accuracy

def streaming_search(query, callback):
    """
    Streaming search that calls callback at each step for progress updates.
    """
    callback("Enriching query…")
    enriched = enrich_user_query_llm(query)

    callback("Vector searching in Chroma…")
    raw, _ = dual_vector_retrieval(query, enriched)

    callback("Running LLM reranker…")
    reranked = rerank_with_llm(query, raw)

    callback("Computing final rankings…")
    # Build final results with scoring
    final = []
    for rank, item in enumerate(reranked):
        meta = item["meta"]
        distance = item["distance"]
        kw_score = keyword_score(query, item["text"])
        fused = fused_rank_score(distance, kw_score, rank)
        
        final.append({
            "video_title": meta["video_title"],
            "timestamp": meta["timestamp"],
            "section_title": meta["section_title"],
            "summary": meta["section_summary"],
            "url": meta["video_url"],
            "chakra": meta["chakra"],
            "published_at": meta.get("published_at", ""),
            "confidence": fused,
        })
    
    # Sort by confidence
    final = sorted(final, key=lambda x: x["confidence"], reverse=True)

    callback("Done.")
    return final

def search(user_query: str, top_k=3, explanations=True):
    enriched = enrich_user_query_llm(user_query)
    vector_hits, _ = dual_vector_retrieval(user_query, enriched)
    reranked = rerank_with_llm(user_query, vector_hits)

    final = []

    for rank, item in enumerate(reranked):
        meta = item["meta"]
        distance = item["distance"]
        kw_score = keyword_score(user_query, item["text"])

        fused = fused_rank_score(distance, kw_score, rank)

        rec = {
            "video_title": meta["video_title"],
            "timestamp": meta["timestamp"],
            "section_title": meta["section_title"],
            "summary": meta["section_summary"],
            "url": meta["video_url"],
            "chakra": meta["chakra"],
            "published_at": meta.get("published_at", ""),
            "confidence": fused,
        }

        if explanations:
            rec["explanation"] = explain_ranking(
                user_query, item, distance, kw_score, rank
            )["explanation"]

        final.append(rec)

    return sorted(final, key=lambda x: x["confidence"], reverse=True)[:top_k]

