import os
import sys
import chromadb
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
import json
import re
from typing import List, Dict
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

# Vector retrieval: fetch this many neighbors per query embedding (original + enriched merged, capped below).
INITIAL_VECTOR_CANDIDATES = 60

# Section-level candidates sent to the reranker (not "videos"). Higher = better recall but more tokens/latency;
# partial ID lists are repaired via _repair_llm_ranked_ids. 60 caused frequent truncation before repair existed.
LLM_RERANK_POOL_MAX = 45

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

def chroma_vector_search(query: str, n_results=12):
    query_embedding = get_embedding(query)
    # Note: Chroma query() include does not support "ids" in many versions; dedupe uses metadata keys.
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
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
            "confidence": confidence,
        })

    # 5 | Top K only
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

def recommend_related(section_embedding, top_k=5):
    results = collection.query(
        query_embeddings=[section_embedding],
        n_results=top_k+1,
        include=["documents","metadatas","distances"]
    )
    # skip the first one (same section)
    return [
        {
            "section_title": results["metadatas"][0][i]["section_title"],
            "video_title": results["metadatas"][0][i]["video_title"],
            "timestamp": results["metadatas"][0][i]["timestamp"],
            "url": results["metadatas"][0][i]["video_url"],
        }
        for i in range(1, top_k+1)
    ]

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

