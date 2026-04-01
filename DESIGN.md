# Video & Resource Search System — Design Document

## Table of Contents

1. [System Overview](#system-overview)
2. [Repository Layout](#repository-layout)
3. [Architecture](#architecture)
4. [Major Components](#major-components)
5. [Data Flow](#data-flow)
6. [Storage Layer](#storage-layer)
7. [Search Pipeline](#search-pipeline)
8. [API Layer](#api-layer)
9. [Frontend](#frontend)
10. [Key Algorithms](#key-algorithms)
11. [Dependencies](#dependencies)
12. [Configuration](#configuration)
13. [Performance & Error Handling](#performance--error-handling)
14. [Future Enhancements](#future-enhancements)

---

## System Overview

This project is a **Sahaja Yoga–oriented discovery stack** that:

- **Ingests** YouTube playlist videos: cleans descriptions, extracts video-level and timestamp-level content, embeds with OpenAI, and stores vectors in **ChromaDB**.
- **Searches** those sections with **semantic retrieval + LLM reranking** (OpenAI), exposed via a **Flask** API and a single-page **HTML** UI.
- **Ingests and searches** separate **handout/resource** documents in a **second ChromaDB collection**, with REST endpoints and dedicated UI pages.

### Key Features

- Playlist-driven video ingestion with configurable batch size and sort order
- Livestream-aware eligibility (completed broadcasts only, with a post-end cooldown)
- Video-level and per-timestamp embeddings and metadata
- Resource (PDF/handout) ingestion with duplicate URL checks
- Hybrid-style search: embeddings + keyword overlap on results + LLM rerank
- CLI utilities to browse Chroma and delete all entries for a given `video_id`

---

## Repository Layout

```
playpen/
├── api/
│   └── flask_api_server.py    # HTTP API + static UI routes
├── resources/
│   ├── video_processing.py    # YouTube → Chroma (videos)
│   ├── browse_videos.py       # CLI: browse / stats / delete-video
│   ├── resource_ingestion.py  # Chroma (handouts)
│   └── browse_resources.py    # CLI: browse resources collection
├── search/
│   ├── video_search.py        # Video semantic search + rerank
│   ├── resource_search.py     # Resource semantic search + rerank
│   ├── postprocess.py         # Fused scores, explanations, helpers
│   └── hybrid_search.py       # Optional / experimental
├── ui/
│   ├── search.html            # Main app: video + handout search
│   ├── resource_form.html     # Add resource
│   ├── resource_update.html   # Update resource by ID
│   ├── resource_search.html   # Standalone resource search page
│   └── …                      # Static assets (e.g. images) via /ui/…
├── config.py                  # Paths + API key loaders
├── requirements.txt
├── sahajyoga_recent5_audit.csv   # Written by video_processing (audit)
└── DESIGN.md
```

**Secrets (local only, not committed):** `api_key.txt`, `openai_api_key.txt` — see `.gitignore`.

---

## Architecture

```
┌─────────────────┐
│  YouTube API    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Video processing                    │
│  resources/video_processing.py       │
│  - Playlist resolve by title         │
│  - Livestream / publish gating       │
│  - clean_description, timestamps     │
│  - OpenAI embeddings                 │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  ChromaDB (PersistentClient)         │
│  resources/chroma_free_store/         │
│  • sahajayoga_21_days_videos         │
│  • sahajayoga_resources              │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Search                              │
│  search/video_search.py              │
│  search/resource_search.py           │
│  - Query enrichment (OpenAI)         │
│  - Vector query                      │
│  - LLM rerank                        │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Flask API                           │
│  api/flask_api_server.py (port 5005)│
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  ui/search.html + other HTML pages   │
└─────────────────────────────────────┘
```

---

## Major Components

### 1. Video ingestion (`resources/video_processing.py`)

**Purpose:** Pull videos from configured playlists, enrich text, embed, write Chroma + CSV audit.

**Configuration (in file):**

- `CHANNEL_ID` — YouTube channel whose playlists are listed
- `TARGET_PLAYLIST_TITLES` — exact playlist titles matched case-insensitively
- `MAX_RECENT_VIDEOS` — max videos considered **per playlist** after filtering/sort
- `PROCESS_OLDEST_FIRST` — if `True`, oldest eligible videos first; if `False`, newest first
- `OUTPUT_CSV` — audit filename (default under project root)

**Playlist resolution:** `get_playlist_id_from_title(title)` lists channel playlists and matches title.

**Eligibility / gating (`list_videos_in_playlist`):**

- Skips playlist items without `videoPublishedAt`
- For each candidate, calls `videos().list(part="snippet,liveStreamingDetails,contentDetails")`:
  - Skip if `snippet.liveBroadcastContent` is `live` or `upcoming`
  - If `liveStreamingDetails` exists, require `actualEndTime` and that end time is **at least 12 hours** before “now”
- Logs per-video skip reasons and a summary count (debug)

**Processing (`process_playlist`):**

- Skips a video if Chroma already has id `{video_id}_video`
- Uses `videos().list` snippet for description and canonical `publishedAt`
- `clean_description` → `extract_video_level_enrichment` → `parse_timestamps`
- `build_embedding_text` + OpenAI `text-embedding-3-large` → `collection.add`
- IDs: `{video_id}_video`, `{video_id}_{i}_ts`

**Collection dimension:** On startup, may delete and recreate the collection if stored embeddings are incompatible with **3072** dimensions (legacy 384-dim collections).

---

### 2. Resource ingestion (`resources/resource_ingestion.py`)

**Purpose:** Ingest handouts into collection `sahajayoga_resources` (same Chroma directory as videos).

- Validates fields; enforces unique `download_url`
- IDs like `resource_001`, …
- OpenAI embeddings for resource text; metadata excludes raw embedding fields from Chroma metadata rules

**Browse CLI:** `resources/browse_resources.py` (stats, list, topic, get, clear).

---

### 3. Video search (`search/video_search.py`)

**Purpose:** End-user video section search.

**Pipeline (`search_video_sections`):**

1. `enrich_user_query_llm` — OpenAI `gpt-4o-mini` (falls back to original query if no client)
2. `chroma_vector_search(enriched, n_results=12)` — OpenAI `text-embedding-3-large` query embedding
3. `rerank_with_llm(original_query, raw)` — OpenAI `gpt-4o-mini` returns JSON id order; falls back to distance order on failure
4. `keyword_score` + `compute_confidence(distance, keyword_boost)` on reranked list
5. Return top `top_k` (default 3 from API)

**Also:** `conversational_search`, `streaming_search`, `search(..., explanations=True)` using `postprocess.fused_rank_score` / `explain_ranking` where applicable.

---

### 4. Resource search (`search/resource_search.py`)

Same pattern as video search: enrichment → vector search → LLM rerank → confidence; collection `sahajayoga_resources`.

---

### 5. Post-processing (`search/postprocess.py`)

- `fused_rank_score` — combines distance, keyword overlap, LLM rank position (used in `search()` / `streaming_search`, not the simple `search_video_sections` path)
- `explain_ranking`, `merge_adjacent_sections`, `convert_ts`, `to_ui_card`

---

### 6. Video Chroma CLI (`resources/browse_videos.py`)

Commands include: `stats`, `all`, `video <id>`, `playlist`, `type`, `summaries`, `timestamps`, `get`, `search`, `clear`, **`delete-video <video_id> [--dry-run] [--yes]`** (removes Chroma rows for that `video_id` and matching rows from `sahajyoga_recent5_audit.csv` when confirmed).

---

### 7. Flask API (`api/flask_api_server.py`)

- Serves UI from `ui/`; static files under `/ui/<filename>`
- Default bind: **port 5005** (`debug=True` in `__main__`)

---

## Data Flow

### Ingestion (video)

```
TARGET_PLAYLIST_TITLES
  → playlist ID
  → list + filter playlist items (publish time present)
  → per video: videos().list (live / ended / actualEndTime rules)
  → sort + cap (MAX_RECENT_VIDEOS, PROCESS_OLDEST_FIRST)
  → skip if {video_id}_video exists
  → clean_description → enrich → timestamps → embed → Chroma add
  → append rows to OUTPUT_CSV
```

### Ingestion (resource)

```
POST /api/resources/ingest (or UI form)
  → validate → embed → Chroma add (sahajayoga_resources)
```

### Search (video)

```
POST /search { query, top_k? }
  → enrich (OpenAI)
  → vector search (top 12)
  → LLM rerank
  → confidence + top_k JSON
```

---

## Storage Layer

### Paths

- **Chroma persist directory:** `resources/chroma_free_store/` (from `config.get_chroma_dir()`)
- SQLite and index files live under that directory

### Collection: `sahajayoga_21_days_videos`

| Aspect | Detail |
|--------|--------|
| Embeddings | OpenAI `text-embedding-3-large` (**3072** dimensions) |
| Space | Cosine (metadata `hnsw:space: cosine` on create) |
| Document | `embedding_text` built in `build_embedding_text` |
| Video-level ID | `{video_id}_video` |
| Timestamp ID | `{video_id}_{index}_ts` |

**Metadata (typical):** `video_id`, `video_title`, `playlist_id`, `type`, `chakra`, `quote`, `hashtags`, `timestamp`, `section_title`, `section_summary`, `video_url`, `published_at`

### Collection: `sahajayoga_resources`

Handouts: `resource_id`, title, description, topic, tags (as string in metadata), `download_url`, `file_type`, timestamps, etc.

---

## Search Pipeline

### Practical stages (video)

1. **Semantic retrieval** — single embedding query, top-N neighbors in Chroma (fixed `n_results=12` in code today)
2. **LLM rerank** — reorder candidates for query relevance (including date-aware rules when the model follows prompt)
3. **Lexical boost** — `keyword_score` on original query vs document text; folded into `compute_confidence`

**Note:** Paraphrased queries can miss relevant timestamps if they fall outside the top-N retrieved set; improving recall is a known improvement area (e.g. larger N or dual-query retrieval).

### `search()` / streaming variant

Uses **fused** scoring from `postprocess.py` (embedding + keyword + rank position weights).

---

## API Layer

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Main UI (`search.html`) |
| GET | `/ui/<path>` | Static assets under `ui/` |
| POST | `/search` | Video section search |
| GET | `/health` | Health check |
| POST | `/api/resources/ingest` | Create resource |
| POST | `/api/resources/search` | Search resources |
| GET | `/api/resources/<id>` | Get resource |
| PUT | `/api/resources/<id>` | Update resource |
| GET | `/resource-form`, `/resource_form.html` | Add resource UI |
| GET | `/resource-update`, `/resource_update.html` | Update resource UI |
| GET | `/resources`, `/resource-search`, … | Resource search UI |

**Errors:** 400 for bad JSON / validation; 409 duplicate `download_url`; 404 missing resource; 500 with message on unexpected failures.

---

## Frontend

- **`ui/search.html`** — Centered title bar, hero row (image + search panel), video vs handout modes, mentor link, results cards, YouTube modal
- **`resource_form.html`**, **`resource_update.html`**, **`resource_search.html`** — Resource workflows

Relative URLs (`/search`, `/api/resources/search`) require the Flask server (not `file://`).

---

## Key Algorithms

### Chakra detection

Keyword scoring over `CHAKRA_MAP` in `video_processing.py` (word-boundary matches).

### Timestamp parsing

Regex `(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)`; summary = lightweight sentence join of following lines until next timestamp.

### Description cleaning

Removes boilerplate block and lines with URLs, session/day noise, ALL-CAPS lines, etc. (see `clean_description`).

### Query enrichment & reranking

Implemented with OpenAI chat models and structured prompts in `video_search.py` / `resource_search.py` (not Ollama).

---

## Dependencies

| Package | Role |
|---------|------|
| `chromadb` | Vector store |
| `openai` | Embeddings + chat (enrichment, rerank) |
| `tenacity` | Retries on OpenAI calls |
| `flask` | API + static hosting |
| `google-api-python-client` | YouTube Data API v3 |
| `numpy` | Used by optional `hybrid_search.py` (with `faiss-cpu`) |
| `faiss-cpu`, `requests` | Optional extras in `requirements.txt` (hybrid search experiments, ingest smoke test) |

---

## Configuration

| Item | Source |
|------|--------|
| YouTube API key | `api_key.txt` (project root), `config.load_yt_api_key()` |
| OpenAI API key | `openai_api_key.txt`, `config.load_openai_api_key()` |
| Chroma directory | `config.get_chroma_dir()` → `resources/chroma_free_store` |
| Playlists / channel / batch / order | `resources/video_processing.py` constants |
| Flask port | `api/flask_api_server.py` → `5005` |

---

## Performance & Error Handling

- **Ingestion:** Sequential per video; OpenAI embedding calls are network-bound; debug skip logs add console noise only
- **Search:** Typically dominated by OpenAI round-trips (enrichment + rerank) plus one embedding call
- **Fallbacks:** Missing OpenAI client → skip enrichment / rerank where coded; Chroma errors surface to API as 500

---

## Future Enhancements

- Larger candidate pool and/or dual-query retrieval to improve recall on paraphrases
- Optional BM25 or keyword pre-filter merged with vector hits
- HTTP DELETE for video-by-id (CLI already exists)
- Caching for repeated queries
- Stricter secret handling and env-based config

---

## Conclusion

The codebase centers on **OpenAI-powered embeddings and reranking** over **ChromaDB** with a clear split between **video sections** and **handout resources**, a **Flask** façade, and **HTML** clients. Ingestion is playlist-driven with explicit **livestream completion** rules and configurable **batch ordering**. This document reflects the implementation as of the last update to `DESIGN.md`.
