# Testing Guide

This guide matches the current codebase and [DESIGN.md](./DESIGN.md). Project root: `/Users/pawansaxena/playpen/21days-media-resources`.

For a fast sanity check, run `./quick_test.sh` from the project root.

---

## Prerequisites

### 1. Install dependencies

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

# Use pip3 if `pip` is not on your PATH (common on macOS)
pip3 install -r requirements.txt

# Verify key packages
pip3 list | grep -E "chromadb|flask|openai|google-api|tenacity"
```

### 2. API keys (project root)

| File | Purpose |
|------|---------|
| `api_key.txt` | YouTube Data API v3 (ingestion) |
| `openai_api_key.txt` | OpenAI (embeddings + query enrichment + reranking for search) |

Create at **project root** (same folder as `config.py`):

```bash
echo "YOUR_YOUTUBE_API_KEY" > api_key.txt
echo "YOUR_OPENAI_API_KEY" > openai_api_key.txt
```

YouTube key setup: [Google Cloud Console](https://console.cloud.google.com/) → enable YouTube Data API v3 → create API key.

**Note:** Search and resource search use **OpenAI** (`gpt-4o-mini`, `text-embedding-3-large`), not Ollama. You do not need Ollama running for the main app flow.

---

## Step 1: Video ingestion

### Run the pipeline

```bash
cd /Users/pawansaxena/playpen/21days-media-resources
python3 resources/video_processing.py
```

### Process one specific video by `video_id` (manual re-add flow)

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

# Process exactly one video
python3 resources/video_processing.py --video-id 1BTlbtXVMRg

# Replace existing rows for that video_id
python3 resources/video_processing.py --video-id 1BTlbtXVMRg --overwrite
```

On startup you should see processing order and per-playlist limit from `PROCESS_OLDEST_FIRST` and `MAX_RECENT_VIDEOS` in `resources/video_processing.py`.

**Expected behavior:**

- Resolves playlists listed in `TARGET_PLAYLIST_TITLES` (titles must match the channel’s playlists).
- Skips videos that already have `{video_id}_video` in Chroma.
- Applies **livestream rules**: skips `live` / `upcoming`; if `liveStreamingDetails` exists, requires `actualEndTime` at least **12 hours** ago (see DESIGN.md). Skip reasons are logged.
- Writes embeddings to Chroma under `resources/chroma_free_store/`.
- Appends audit rows to `sahajyoga_recent5_audit.csv` in the project root.

### Verify on disk

```bash
ls -la resources/chroma_free_store/
ls -la sahajyoga_recent5_audit.csv
head -5 sahajyoga_recent5_audit.csv
```

---

## Step 2: Chroma browser (videos)

CLI: `resources/browse_videos.py` (from project root).

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

# Statistics
python3 resources/browse_videos.py stats

# List entries (optional limit)
python3 resources/browse_videos.py all 10

# All entries for one YouTube video ID
python3 resources/browse_videos.py video VIDEO_ID_HERE
python3 resources/browse_videos.py video VIDEO_ID_HERE --full

# By playlist ID
python3 resources/browse_videos.py playlist PLAYLIST_ID_HERE

# By type
python3 resources/browse_videos.py type video_context
python3 resources/browse_videos.py type timestamp_section

python3 resources/browse_videos.py summaries
python3 resources/browse_videos.py timestamps
python3 resources/browse_videos.py timestamps VIDEO_ID_HERE

# Single Chroma entry by id (e.g. VIDEO_ID_video)
python3 resources/browse_videos.py get VIDEO_ID_video

# Semantic search against collection (uses OpenAI embedding)
python3 resources/browse_videos.py search "meditation" 5
```

### Delete all data for one video (optional)

**Dry-run by default** (no deletion):

```bash
python3 resources/browse_videos.py delete-video VIDEO_ID_HERE
```

**Actually delete** (Chroma rows for that `video_id` + matching rows in `sahajyoga_recent5_audit.csv`):

```bash
python3 resources/browse_videos.py delete-video VIDEO_ID_HERE --yes
```

### Clear entire video collection (destructive)

```bash
python3 resources/browse_videos.py clear --yes
```

---

## Step 3: Resources (handouts)

### Browse resource collection

```bash
python3 resources/browse_resources.py stats
python3 resources/browse_resources.py all 10
python3 resources/browse_resources.py get resource_001
```

### Ingest via API (server must be running — see Step 4)

```bash
curl -X POST http://localhost:5005/api/resources/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Sample Handout",
    "description": "A short description of the handout content for search.",
    "topic": "Meditation",
    "tags": ["meditation", "beginner"],
    "download_url": "https://example.com/handout.pdf",
    "file_type": "pdf"
  }'
```

Or use the UI: `http://localhost:5005/resource-form`

---

## Step 4: Search (Python, no server)

### Basic video search

```bash
cd /Users/pawansaxena/playpen/21days-media-resources
python3 -c "
from search.video_search import search_video_sections
q = 'meditation techniques'
r = search_video_sections(q, top_k=3)
print(q, '->', len(r), 'results')
for i, x in enumerate(r, 1):
    print(i, x['video_title'], '|', x['section_title'], '|', x.get('timestamp',''))
"
```

### Search with explanations (`postprocess` fused path)

```bash
python3 -c "
from search.video_search import search
for x in search('heart chakra', top_k=3, explanations=True):
    print(x['video_title'], x.get('explanation','')[:120], '...')
"
```

### Query enrichment preview

```bash
python3 -c "
from search.video_search import enrich_user_query_llm
for q in ['meditation', 'heart chakra']:
    print(q, '=>', enrich_user_query_llm(q))
"
```

Requires valid `openai_api_key.txt`. If the client is missing, enrichment returns the original query.

### Debug trace

```python
from search.video_search import debug_trace
debug_trace("meditation")
```

---

## Step 5: Flask API

### Start server

```bash
cd /Users/pawansaxena/playpen/21days-media-resources
python3 api/flask_api_server.py
```

Expect: `http://127.0.0.1:5005` (see `flask_api_server.py`).

### Health

```bash
curl http://localhost:5005/health
```

### Video search

```bash
curl -X POST http://localhost:5005/search \
  -H "Content-Type: application/json" \
  -d '{"query": "meditation", "top_k": 3}'
```

### Video ingest by `video_id` (new API)

```bash
# Add one video (fails with 409 if already present)
curl -X POST http://localhost:5005/api/videos/ingest \
  -H "Content-Type: application/json" \
  -d '{"video_id":"1BTlbtXVMRg"}'

# Replace existing rows for that video_id
curl -X POST http://localhost:5005/api/videos/ingest \
  -H "Content-Type: application/json" \
  -d '{"video_id":"1BTlbtXVMRg","overwrite":true}'
```

### Resource search

```bash
curl -X POST http://localhost:5005/api/resources/search \
  -H "Content-Type: application/json" \
  -d '{"query": "handout", "top_k": 5}'
```

### Error checks (video search)

```bash
# 400 missing query
curl -X POST http://localhost:5005/search \
  -H "Content-Type: application/json" \
  -d '{"top_k": 3}'

# 400 empty query
curl -X POST http://localhost:5005/search \
  -H "Content-Type: application/json" \
  -d '{"query": ""}'
```

---

## Step 6: Frontend

The UI calls relative URLs (`/search`, `/api/resources/search`). **Use the Flask app**, not `file://`.

1. Start Flask (Step 5).
2. Open **`http://localhost:5005/`** (main search UI).
3. Try **Videos** vs **Search Handouts** toggles.
4. Optional pages:
   - `http://localhost:5005/resource-form` — add resource  
   - `http://localhost:5005/resource-update` — update by resource id  
   - `http://localhost:5005/resources` — resource search page  

Static assets (e.g. images under `ui/`) are served at `/ui/<filename>`.

---

## Step 7: End-to-end checklist

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

# Optional: clear video collection (destructive)
# python3 resources/browse_videos.py clear --yes

# Ingest (configure playlists first in video_processing.py)
python3 resources/video_processing.py

# Inspect
python3 resources/browse_videos.py stats

# Search from Python
python3 -c "from search.video_search import search_video_sections; print(len(search_video_sections('meditation',3)))"

# API + browser
python3 api/flask_api_server.py
# other terminal:
curl -s http://localhost:5005/health
open http://localhost:5005/
```

---

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| `pip` not found | Use `pip3` |
| YouTube errors | `api_key.txt` at project root; quotas; playlist titles match exactly |
| OpenAI / search failures | `openai_api_key.txt`; billing; model access |
| Collection missing / empty | Run `resources/video_processing.py`; confirm `resources/chroma_free_store/` |
| Video not ingested | Livestream rules, `MAX_RECENT_VIDEOS` + sort order, or already in DB (`{video_id}_video`) |
| Import errors | `pip3 install -r requirements.txt` from project root |
| Port 5005 in use | `lsof -ti:5005 \| xargs kill` or change port in `flask_api_server.py` |
| Wrong dimension / collection recreated | See DESIGN.md; old 384-dim data may trigger collection recreate |

---

## Performance (rough)

With OpenAI:

- Query enrichment: ~0.5–2 s per call (network + model)
- Embeddings + Chroma query: usually sub-second to a few seconds total (video search uses dual vector queries)
- LLM rerank: ~1–4 s typical  
- **Total** per search often **~3–10 s** depending on latency

---

## Quick test checklist

- [ ] `./quick_test.sh` passes from project root
- [ ] `pip3 install -r requirements.txt`
- [ ] `api_key.txt` and `openai_api_key.txt` in project root
- [ ] `python3 resources/video_processing.py` (if testing ingestion)
- [ ] `python3 resources/video_processing.py --video-id <id> [--overwrite]` (if testing single video ingest)
- [ ] `python3 resources/browse_videos.py stats` shows data
- [ ] `python3 -c "from search.video_search import search_video_sections; ..."` returns results
- [ ] `python3 api/flask_api_server.py` and `curl localhost:5005/health`
- [ ] Browser: `http://localhost:5005/`

---

## Example search queries

- `"meditation"` — broad semantic
- `"heart chakra"` — topic
- `"Shri Mataji"` — quotes / mentions
- Section titles from descriptions — often strong matches when phrasing is close
- `"how to meditate"` — paraphrase (recall may vary; see DESIGN.md)

---

## Related docs

- [DESIGN.md](./DESIGN.md) — architecture, storage, API list, ingestion gates
