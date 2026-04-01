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
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"]
    )

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
        print("⚠️ OpenAI client not initialized. Using original order.")
        return sorted(items, key=lambda x: x["distance"])

    try:
        # Extract published_at dates for date-based ranking
        items_with_dates = []
        for item in items:
            published_at = item["meta"].get("published_at", "")
            items_with_dates.append({
                **item,
                "published_at": published_at
            })
        
        # Detect if query mentions a date
        date_patterns = [
            r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\s+\d{4}',
            r'\b\d{1,2}(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}',
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?\s+\d{4}',
            r'\b\d{4}-\d{2}-\d{2}',
            r'\b\d{1,2}/\d{1,2}/\d{4}',
        ]
        has_date_mention = any(re.search(pattern, original_query, re.IGNORECASE) for pattern in date_patterns)
        
        # Build prompt with semantic relevance as primary focus
        # Simplify items structure for LLM - only include essential fields
        simplified_items = []
        for item in items_with_dates:
            simplified_items.append({
                "id": item["id"],
                "text": item["text"][:200] + "..." if len(item["text"]) > 200 else item["text"],  # Truncate long text
                "published_at": item.get("published_at", ""),
                "video_title": item["meta"].get("video_title", "")[:100]  # Truncate long titles
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
    base = max(0, 1 - distance)
    return round(base + (0.15 * keyword_boost), 3)

def search_video_sections(user_query: str, top_k=3):
    # 1 | Enrich query
    enriched = enrich_user_query_llm(user_query)

    # 2 | Vector search
    raw = chroma_vector_search(enriched, n_results=12)

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

    raw = chroma_vector_search(enriched)
    print("\nRaw vector matches:", len(raw["documents"][0]))

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
    raw = chroma_vector_search(enriched, n_results=20)

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
    vector_hits = chroma_vector_search(enriched, n_results=20)
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

