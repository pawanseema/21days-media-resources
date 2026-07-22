"""
Resource search service for meditation handouts.

Provides semantic search functionality for resources stored in ChromaDB,
using vector search with LLM reranking for improved relevance.
"""

import os
import sys
import chromadb
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
import json
import re
from typing import List, Dict

# Add project root to Python path for config import
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import load_openai_api_key, get_chroma_dir

CHROMA_DIR = get_chroma_dir()
COLLECTION_NAME = "sahajayoga_resources"

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
try:
    collection = client.get_collection(name=COLLECTION_NAME)
except Exception:
    # Collection doesn't exist, create it
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )


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


def enrich_user_query_llm(user_query: str):
    """
    Enrich user query for better search results.
    Focuses on Sahaja Yoga meditation topics and resources.
    """
    system = """You are a search query optimizer for Sahaja Yoga meditation resources and handouts. 
Your task is to rewrite queries to be more search-optimized while preserving all specific information.
- Focus on Sahaja Yoga meditation topics, chakras, vibrations, footsoak, guided meditations, handouts, resources
- Preserve specific terms, topics, and keywords
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


def chroma_vector_search(query: str, n_results=12):
    """Perform vector search in ChromaDB."""
    query_embedding = get_embedding(query)
    return chroma_query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"]
    )


def keyword_score(query: str, text: str) -> float:
    """Calculate keyword overlap score between query and text."""
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
        print("⚠️ OpenAI client not initialized. Using original order.")
        return sorted(items, key=lambda x: x["distance"])

    try:
        # Build items list for LLM
        items_with_context = []
        for item in items:
            items_with_context.append({
                "id": item["id"],
                "title": item["meta"].get("title", ""),
                "description": item["meta"].get("description", ""),
                "topic": item["meta"].get("topic", ""),
                "tags": ", ".join(item["meta"].get("tags", [])),
                "text": item["text"]
            })
        
        # Build prompt for semantic relevance ranking
        user_prompt = f"""
User query: "{original_query}"

Rank these search results from MOST relevant to LEAST relevant based on semantic relevance to the query.

Rank by how well each result's content (title, description, topic, tags) semantically matches the meaning and intent of the user query. Consider the content, topics, and context.

Results: {json.dumps(items_with_context, indent=2)}

Return ONLY a JSON list of IDs in rank order (e.g., [2,0,1]).
"""
        system_content = "You are a search ranking assistant. Rank results by semantic relevance to the query. Return ONLY a JSON list of IDs in rank order, nothing else."

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt}
            ]
        )
        response_text = response.choices[0].message.content.strip()
        
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'\[[\d,\s]+\]', response_text)
        if json_match:
            response_text = json_match.group(0)
        
        # Try to parse JSON, fall back to original order if parsing fails
        try:
            ranked_ids = json.loads(response_text)
            # Validate ranked_ids
            if isinstance(ranked_ids, list):
                if len(ranked_ids) == 0:
                    print(f"Warning: LLM returned empty array. Using original order.")
                    return sorted(items, key=lambda x: x["distance"])
                if len(ranked_ids) == len(items):
                    # Validate all IDs are valid indices
                    if all(isinstance(id, int) and 0 <= id < len(items) for id in ranked_ids):
                        # Check if all IDs are present exactly once
                        if len(set(ranked_ids)) == len(ranked_ids):
                            return [items[i] for i in ranked_ids]
                        else:
                            print(f"Warning: Duplicate IDs in LLM response. Using original order.")
                    else:
                        print(f"Warning: Invalid IDs in LLM response. Using original order.")
                else:
                    print(f"Warning: LLM returned {len(ranked_ids)} IDs but expected {len(items)}. Using original order.")
        except (json.JSONDecodeError, ValueError, IndexError) as e:
            print(f"Warning: Failed to parse LLM reranking response: {e}. Using original order.")
            print(f"Response was: {response_text[:200]}")  # Truncate long responses
    
    except Exception as e:
        print(f"Warning: LLM reranking failed: {e}. Using original order.")
    
    # Fallback: return items in original order (sorted by distance)
    return sorted(items, key=lambda x: x["distance"])


def compute_confidence(distance, keyword_boost):
    """Compute confidence score from distance and keyword boost."""
    base = max(0, 1 - distance)
    return round(base + (0.15 * keyword_boost), 3)


def search_resources(user_query: str, top_k=5):
    """
    Search for resources based on user query.
    
    Args:
        user_query: Search query string
        top_k: Number of top results to return (default: 5)
    
    Returns:
        List of resource results with metadata
    """
    # 1 | Enrich query
    enriched = enrich_user_query_llm(user_query)

    # 2 | Vector search
    raw = chroma_vector_search(enriched, n_results=12)

    # Check if we have any results
    if not raw or not raw.get("documents") or len(raw["documents"][0]) == 0:
        return []

    # 3 | Re-rank with LLM
    reranked = rerank_with_llm(user_query, raw)

    # 4 | Build output with scoring
    out = []
    for item in reranked:
        meta = item["meta"]
        keyword_boost = keyword_score(user_query, item["text"])
        confidence = compute_confidence(item["distance"], keyword_boost)

        # Parse tags from comma-separated string back to array
        tags_str = meta.get("tags", "")
        tags_list = []
        if tags_str:
            if isinstance(tags_str, list):
                tags_list = tags_str
            elif isinstance(tags_str, str):
                tags_list = [tag.strip() for tag in tags_str.split(",") if tag.strip()]

        out.append({
            "resource_id": meta.get("resource_id", ""),
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "topic": meta.get("topic", ""),
            "tags": tags_list,
            "download_url": meta.get("download_url", ""),
            "file_type": meta.get("file_type", ""),
            "created_at": meta.get("created_at", ""),
            "confidence": confidence,
        })

    # 5 | Highest confidence first, then top K
    out.sort(key=lambda x: x.get("confidence") or 0, reverse=True)
    return out[:top_k]

