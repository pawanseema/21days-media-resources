# Workaround for Python 3.9 compatibility with packages_distributions
import sys
if sys.version_info < (3, 10):
    import importlib.metadata
    if not hasattr(importlib.metadata, 'packages_distributions'):
        try:
            # Use backported importlib_metadata package
            import importlib_metadata
            importlib.metadata.packages_distributions = importlib_metadata.packages_distributions
        except (ImportError, AttributeError):
            # Create a minimal stub function
            def _packages_distributions():
                return {}
            importlib.metadata.packages_distributions = _packages_distributions

import os, re, csv
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from googleapiclient.discovery import build
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
import chromadb

# Add project root to Python path for config import
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import load_yt_api_key, load_openai_api_key, get_chroma_dir

# -----------------------------
# CONFIGURATION
# -----------------------------
YT_API_KEY = load_yt_api_key()
OPENAI_API_KEY = load_openai_api_key()
CHANNEL_ID = "UCIXjmjecxu7_LVAMCd9063w"  # Replace with your channel ID

TARGET_PLAYLIST_TITLES = [
    "21 Day Meditation Course, West Coast 2025",
    "21 Days Meditation Course - January 2026"
]

MAX_RECENT_VIDEOS = 35  # Only process N videos per playlist per run
PROCESS_OLDEST_FIRST =  True # False=newest first, True=oldest first
OUTPUT_CSV = "sahajyoga_recent5_audit.csv"
CHROMA_DIR = get_chroma_dir()

# Ensure persist directory exists
os.makedirs(CHROMA_DIR, exist_ok=True)

# -----------------------------
# YouTube Client
# -----------------------------
youtube = build("youtube", "v3", developerKey=YT_API_KEY)

# -----------------------------
# Models
# -----------------------------
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Use PersistentClient to ensure data is saved to disk
client = chromadb.PersistentClient(path=CHROMA_DIR)
COLLECTION_NAME = "sahajayoga_21_days_videos"

# Check if collection exists and has correct dimension
# If it was created with old 384-dim model, we need to recreate it
try:
    existing_collection = client.get_collection(name=COLLECTION_NAME)
    # Collection exists - test if it accepts correct dimension (3072)
    # Even empty collections have their dimension set, so we must test
    count = existing_collection.count()
    test_embedding = [0.0] * 3072  # text-embedding-3-large dimension
    test_id = "__dimension_test__"
    
    try:
        # Try to add a test embedding with correct dimension
        # This will fail if dimension is wrong, even for empty collections
        existing_collection.add(
            embeddings=[test_embedding],
            documents=["test"],
            ids=[test_id]
        )
        # If successful, remove the test entry and use the collection
        existing_collection.delete(ids=[test_id])
        collection = existing_collection
    except Exception as e:
        error_msg = str(e).lower()
        if "dimension" in error_msg or "384" in error_msg or "3072" in error_msg:
            # Collection has wrong dimension, delete and recreate
            print("⚠️  Existing collection has wrong embedding dimension.")
            if count > 0:
                print(f"   Found {count} entries with old dimension (384). Deleting and recreating...")
            else:
                print("   Empty collection has wrong dimension. Deleting and recreating...")
            client.delete_collection(name=COLLECTION_NAME)
            collection = client.create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            print("✅ Created new collection with dimension 3072 (text-embedding-3-large)")
        else:
            # Some other error, re-raise it
            raise
except Exception as e:
    # Collection doesn't exist or get_collection failed, create it
    if "not found" in str(e).lower() or "does not exist" in str(e).lower():
        collection = client.create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
    else:
        # Unexpected error, try get_or_create as fallback
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )

# -----------------------------
# Chakra Map
# -----------------------------
CHAKRA_MAP = {
    "Mooladhara": ["innocence", "wisdom", "purity", "harmony", "purity", "chastity"],
    "Swadhisthana": ["creativity", "attention", "pure knowledge", "dynamism", "inspiration"],
    "Nabhi": ["peace", "satisfaction", "generosity", "dignity"],
    "Anahata": ["heart", "love", "compassion", "courage", "security", "benevolence"],
    "Vishuddhi": ["communication", "collectivity", "respect", "witness", "sweetness"],
    "Agnya": ["forgiveness", "ego", "thoughtless awareness", "witness", "super ego"],
    "Sahasrara": ["integration", "joy", "oneness", "divine connection", "enlightenment"],
    "Left Side": ["desire", "emotions", "past"],
    "Right Side": ["future", "planning", "goals"],
    "Central Path": ["balance", "evolution", "present"]
}

# -----------------------------
# HELPERS
# -----------------------------
def detect_chakra(text):
    text_l = text.lower()
    best_chakra = "General"
    best_score = 0

    for chakra, words in CHAKRA_MAP.items():
        terms = set([chakra.lower(), *words])
        score = 0
        for term in terms:
            pattern = rf"\b{re.escape(term.lower())}\b"
            score += len(re.findall(pattern, text_l))
        if score > best_score:
            best_score = score
            best_chakra = chakra

    return best_chakra if best_score > 0 else "General"

def summarize(text, max_sentences=7):
    """Lightweight summary using regex and truncation."""
    sentences = re.split(r"(?<=[.!?]) +", text.strip())
    return " ".join(sentences[:max_sentences])


def build_embedding_text(row):
    """Builds the textual context for embeddings based on record type."""
    # Format published_at for embedding (extract date part for better searchability)
    published_date = ""
    if row.get("published_at"):
        try:
            # Extract date part from ISO format (e.g., "2025-11-16T12:00:00Z" -> "November 16, 2025")
            pub_dt = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            published_date = pub_dt.strftime("%B %d, %Y")  # e.g., "November 16, 2025"
        except (ValueError, AttributeError):
            published_date = row.get("published_at", "")
    
    if row["type"] == "video_context":
        return (
            f"Video: {row['video_title']}\n"
            f"Published: {published_date}\n"
            f"Summary: {row['section_summary']}\n"
            f"Chakra: {row.get('chakra', '')}\n"
            f"Quote: {row.get('quote', '')}\n"
            f"Hashtags: {row.get('hashtags', '')}"
        )
    else:  # timestamp_section
        return (
            f"Video: {row['video_title']}\n"
            f"Published: {published_date}\n"
            f"Section at {row['timestamp']} - {row['section_title']}\n"
            f"Summary: {row['section_summary']}\n"
            f"Chakra: {row.get('chakra', '')}\n"
            f"Quote: {row.get('quote', '')}\n"
            f"Hashtags: {row.get('hashtags', '')}"
        )

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

def list_playlists(channel_id):
    playlists = []
    next_page = None
    while True:
        res = youtube.playlists().list(
            part="snippet",
            channelId=channel_id,
            maxResults=50,
            pageToken=next_page
        ).execute()
        playlists.extend(res["items"])
        next_page = res.get("nextPageToken")
        if not next_page:
            break
    return [{"id": p["id"], "title": p["snippet"]["title"]} for p in playlists]

def get_playlist_id_from_title(title):
    for p in list_playlists(CHANNEL_ID):
        if p["title"].strip().lower() == title.strip().lower():
            return p["id"]
    print(f"⚠️ Playlist not found: {title}")
    return None

def list_videos_in_playlist(playlist_id, max_results=None):
    videos = []
    skip_stats = defaultdict(int)
    next_page = None
    now = datetime.now(timezone.utc)

    def log_skip(video_id, video_title, reason):
        skip_stats[reason] += 1
        print(f"⏭️  Skip {video_id} ({video_title}): {reason}")

    while True:
        res = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page
        ).execute()
        for i in res["items"]:
            vid = i["contentDetails"]["videoId"]
            title = i["snippet"]["title"]
            published = i["contentDetails"].get("videoPublishedAt")
            if not published:
                log_skip(vid, title, "missing videoPublishedAt (scheduled/unpublished)")
                continue

            # Fetch video details for reliable livestream gating.
            # Must process only completed livestreams:
            # 2) skip live/upcoming
            # 3) if liveStreamingDetails exists, actualEndTime must be present
            # 4) actualEndTime must be at least 12 hours ago
            try:
                video_res = youtube.videos().list(
                    part="snippet,liveStreamingDetails,contentDetails",
                    id=vid
                ).execute()
                if not video_res.get("items"):
                    log_skip(vid, title, "no videos().list details returned")
                    continue
                v = video_res["items"][0]
            except Exception as e:
                print(f"⚠️ Unable to fetch video details for {vid}: {e}")
                log_skip(vid, title, "videos().list request failed")
                continue

            snippet = v.get("snippet", {})
            live_broadcast_content = snippet.get("liveBroadcastContent", "none")
            if live_broadcast_content in {"live", "upcoming"}:
                log_skip(vid, title, f"liveBroadcastContent={live_broadcast_content}")
                continue

            live_details = v.get("liveStreamingDetails")
            if live_details is not None:
                actual_end_time = live_details.get("actualEndTime")
                if not actual_end_time:
                    log_skip(vid, title, "live stream missing actualEndTime")
                    continue
                try:
                    actual_end_dt = datetime.fromisoformat(actual_end_time.replace("Z", "+00:00"))
                except ValueError:
                    print(f"⚠️ Unable to parse actualEndTime for video {vid}: {actual_end_time}")
                    log_skip(vid, title, "invalid actualEndTime format")
                    continue
                if actual_end_dt > now or (now - actual_end_dt) < timedelta(hours=12):
                    log_skip(vid, title, "actualEndTime is less than 12 hours ago")
                    continue

            videos.append({
                "video_id": vid,
                "video_title": title,
                "published_at": published
            })
        next_page = res.get("nextPageToken")
        if not next_page:
            break
    # Configurable ordering:
    # - False (default): newest first
    # - True: oldest first
    videos.sort(key=lambda v: v["published_at"], reverse=not PROCESS_OLDEST_FIRST)
    if skip_stats:
        print("📋 Skip summary for playlist scan:")
        for reason, count in sorted(skip_stats.items(), key=lambda x: (-x[1], x[0])):
            print(f"   - {reason}: {count}")
    return videos[:max_results] if max_results else videos

# -----------------------------
def clean_description(text: str) -> str:
    """
    Removes boilerplate section and unwanted lines from YouTube video description.
    - Removes boilerplate section between 'Be ready for life-changing transformative experiences!' 
      and 'JUMP DIRECTLY TO A SECTION USING TIMESTAMPS BELOW'
    - Filters out unwanted lines (URLs, session info, etc.)
    """
    # Step 1: Remove boilerplate section
    pattern = (
        r"Be ready for life-changing transformative experiences!.*?"
        r"JUMP DIRECTLY TO A SECTION USING TIMESTAMPS BELOW"
    )
    cleaned = re.sub(pattern, "JUMP DIRECTLY TO A SECTION USING TIMESTAMPS BELOW", text, flags=re.DOTALL)
    
    # Step 2: Split into lines and filter unwanted lines
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
    cleaned_lines = []
    
    # Filter out unwanted lines:
    # - Lines beginning with URLs
    # - Lines beginning with specific text patterns
    # - words like 'Session', 'DAY', or 'Register'
    # - or are entirely uppercase short titles
    for line in lines:
        if (
            line.startswith(("http://", "https://"))  # Lines beginning with URL
            or re.search(r'https?://', line)  # Lines containing URL anywhere
            or line.startswith("Access recording of all previous sessions - ")
            or line.startswith("In order to join our mentor program, please fill out this form -")
            or re.search(r'\bSession\b', line, re.IGNORECASE)
            or re.search(r'\bDAY\b', line, re.IGNORECASE)
            or line.isupper()
            or line.startswith("JUMP DIRECTLY TO A SECTION")
        ):
            continue
        cleaned_lines.append(line)
    
    # Join back to string
    return "\n".join(cleaned_lines)

# -----------------------------
# PARSE AND ENRICH DESCRIPTION
# -----------------------------
def extract_video_level_enrichment(description: str) -> dict:
    """
    Extracts enrichment data from a cleaned YouTube video description.
    Note: description should already be cleaned by clean_description().
    Extracts:
    - high-level video summary (main descriptive paragraph)
    - founder quote (if present)
    - hashtags at the bottom
    Returns a dict suitable for Chroma enrichment and CSV.
    """
    # Description is already cleaned, just split into lines
    cleaned_lines = [l.strip() for l in description.splitlines() if l.strip()]
    chakra = detect_chakra(description)

    # --- 3️⃣ Determine main paragraph and quote boundaries ---
    timestamp_line_re = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?\s')
    quote_line_idx = None
    timestamp_line_idx = None

    for idx, line in enumerate(cleaned_lines):
        if quote_line_idx is None and line.startswith(("“", '"')):
            quote_line_idx = idx
        if timestamp_line_idx is None and timestamp_line_re.match(line):
            timestamp_line_idx = idx
        if quote_line_idx is not None and timestamp_line_idx is not None:
            break

    cutoff_idx = min(
        quote_line_idx if quote_line_idx is not None else len(cleaned_lines),
        timestamp_line_idx if timestamp_line_idx is not None else len(cleaned_lines)
    )

    main_paragraph_lines = cleaned_lines[:cutoff_idx]
    main_paragraph = " ".join(main_paragraph_lines).strip() if main_paragraph_lines else description.strip()

    # --- 4️⃣ Extract founder quote ---
    founder_quote = None
    if quote_line_idx is not None:
        quote_lines = []
        for line in cleaned_lines[quote_line_idx:]:
            if not line or timestamp_line_re.match(line):
                break
            quote_lines.append(line)
            if "Shri Mataji" in line:
                break
        founder_quote = " ".join(quote_lines).strip()
    if not founder_quote:
        quote_pattern = r'[“"]([^”"]+)[”"]\s*[—-]\s*Shri Mataji[^\n]*'
        quote_match = re.search(quote_pattern, description, flags=re.IGNORECASE)
        founder_quote = quote_match.group(0).strip() if quote_match else None

    # --- 5️⃣ Extract hashtags (bottom lines) ---
    hashtags = re.findall(r"#\w+", description)

    hashtags_text = " ".join(hashtags) if hashtags else ""

    video_level_parts = [main_paragraph]
    if founder_quote:
        video_level_parts.append(founder_quote)
    if hashtags_text:
        video_level_parts.append(hashtags_text)

    print(f"Main paragraph: {main_paragraph}")
    print(f"Founder quote: {founder_quote}")
    print(f"Hashtags: {hashtags}")

    return {
        "video_summary": main_paragraph,
        "founder_quote": founder_quote,
        "hashtags": hashtags,
        "chakra_focus": chakra,
        "video_level_text": "\n\n".join(video_level_parts).strip()
    }

def parse_timestamps(description):
    pattern = r"(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)"
    lines = description.splitlines()
    timestamps = []
    for i, line in enumerate(lines):
        m = re.match(pattern, line.strip())
        if m:
            ts = m.group(1)
            title = m.group(2)
            content = []
            for next_line in lines[i + 1:]:
                if re.match(pattern, next_line.strip()):
                    break
                content.append(next_line.strip())
            summary = summarize(" ".join(content))
            timestamps.append({
                "timestamp": ts,
                "section_title": title,
                "section_summary": summary
            })
    return timestamps

# -----------------------------
# MAIN INGESTION
# -----------------------------
def process_playlist(title):
    """Process one playlist, build enrichment + embeddings, store to Chroma."""
    playlist_id = get_playlist_id_from_title(title)
    if not playlist_id:
        print(f"⚠️ No playlist found for '{title}'")
        return []

    videos = list_videos_in_playlist(playlist_id, MAX_RECENT_VIDEOS)
    rows = []

    for v in videos:
        vid_id = v["video_id"]
        
        # Check if video already exists in ChromaDB (skip if already processed)
        video_entry_id = f"{vid_id}_video"
        existing = collection.get(ids=[video_entry_id])
        if existing["ids"]:
            print(f"⏭️  Skipping video {vid_id} ({v['video_title']}) - already exists in ChromaDB")
            continue
        
        vid_data = youtube.videos().list(part="snippet", id=vid_id).execute()["items"][0]
        original_desc = vid_data["snippet"]["description"]
        url = f"https://www.youtube.com/watch?v={vid_id}"
        
        # Use snippet.publishedAt from videos API (matches YouTube Studio) instead of videoPublishedAt from playlistItems
        actual_published_at = vid_data["snippet"]["publishedAt"]

        desc = clean_description(original_desc)
        enrichment = extract_video_level_enrichment(desc)

        # ---- VIDEO-LEVEL EMBEDDING ----
        video_row = {
            "video_id": vid_id,
            "video_title": v["video_title"],
            "playlist_id": playlist_id,
            "type": "video_context",
            "chakra": enrichment["chakra_focus"],
            "quote": enrichment["founder_quote"],
            "hashtags": ", ".join(enrichment["hashtags"]),
            "timestamp": "",
            "section_title": "Video Summary",
            "section_summary": enrichment["video_summary"],
            "video_url": url,
            "published_at": actual_published_at  # Use snippet.publishedAt from videos API
        }

        video_row["embedding_text"] = build_embedding_text(video_row)
        video_row["embedding"] = get_embedding(video_row["embedding_text"])
        rows.append(video_row)

        # Create metadata dict without embedding fields (ChromaDB metadata only supports simple types)
        metadata_for_chroma = {k: v for k, v in video_row.items() if k not in ["embedding", "embedding_text"]}
        
        collection.add(
            documents=[video_row["embedding_text"]],
            embeddings=[video_row["embedding"]],
            metadatas=[metadata_for_chroma],
            ids=[f"{vid_id}_video"]
        )

        # ---- TIMESTAMP-LEVEL EMBEDDINGS ----
        for i, ts in enumerate(parse_timestamps(desc)):
            ts_row = {
                "video_id": vid_id,
                "video_title": v["video_title"],
                "playlist_id": playlist_id,
                "type": "timestamp_section",
                "chakra": enrichment["chakra_focus"],
                "quote": enrichment["founder_quote"],
                "hashtags": ", ".join(enrichment["hashtags"]),
                "timestamp": ts["timestamp"],
                "section_title": ts["section_title"],
                "section_summary": ts["section_summary"],
                "video_url": url,
                "published_at": actual_published_at  # Use snippet.publishedAt from videos API
            }

            ts_row["embedding_text"] = build_embedding_text(ts_row)
            ts_row["embedding"] = get_embedding(ts_row["embedding_text"])
            rows.append(ts_row)

            # Create metadata dict without embedding fields (ChromaDB metadata only supports simple types)
            metadata_for_chroma = {k: v for k, v in ts_row.items() if k not in ["embedding", "embedding_text"]}
            
            collection.add(
                documents=[ts_row["embedding_text"]],
                embeddings=[ts_row["embedding"]],
                metadatas=[metadata_for_chroma],
                ids=[f"{vid_id}_{i}_ts"]
            )

    print(f"✅ Processed {len(videos)} videos from playlist '{title}'")
    return rows


def write_csv(rows, filename):
    if not rows: return
    keys = rows[0].keys()
    with open(filename, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"✅ Exported {len(rows)} rows to {filename}")

# -----------------------------
# RUN PIPELINE
# -----------------------------
if __name__ == "__main__":
    order_label = "oldest first" if PROCESS_OLDEST_FIRST else "newest first"
    print(f"⚙️  Processing order: {order_label}, limit per playlist: {MAX_RECENT_VIDEOS}")
    all_rows = []
    for title in TARGET_PLAYLIST_TITLES:
        print(f"🎥 Processing playlist: {title}")
        rows = process_playlist(title)
        if rows:
            all_rows.extend(rows)

    write_csv(all_rows, OUTPUT_CSV)
    # Uncomment to embed into Chroma after audit
    # embed_to_chroma(all_rows)
