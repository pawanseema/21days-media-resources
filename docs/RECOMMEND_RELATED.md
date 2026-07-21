# Recommend Related (“More like this”) — Design

**Status:** Design approved for implementation planning (feature-flagged rollout)  
**Related:** [DESIGN.md](../DESIGN.md), [CLOUD_RUN_DEPLOYMENT_PLAN.md](./CLOUD_RUN_DEPLOYMENT_PLAN.md)  
**Existing code:** `search/video_search.py` → `recommend_related(section_embedding, top_k=5)` (unused by API/UI today)

---

## 1. Goals

1. Let users explore **topic-similar video segments** after engaging with one result (“more like this clip”), without re-running their original text query.
2. Keep the **current search experience unchanged** until we deliberately turn the feature on.
3. Ship behind a **feature switch** so we can enable in Cloud Run for testing, then roll out when happy.
4. Prefer **mobile in-modal playback** (no autoplay required) so related exploration stays on our page.

Non-goals (v1):

- Day/course-path navigation as primary UX  
- Auto-related chips on every search result card  
- True “watch completed” detection via YouTube IFrame API (optional later)  
- LLM rerank of related results (optional later for cost/latency)

---

## 2. User experience

### 2.1 Happy path

```
User enters topic query
  → Ranked result cards (confidence descending)  [unchanged]
  → User selects a card
  → In-modal YouTube iframe at segment timestamp
       (desktop: autoplay OK; mobile: user taps Play — no autoplay required)
  → User closes the modal
  → That card shows an explicit [More like this] button
  → User clicks [More like this]
  → Result list is replaced with related segment cards
       Header: Showing more like: {section_title}
       Control: [← Back to Search Result]
  → User can open any related card (same modal flow)
```

### 2.2 Principles

| Principle | Choice |
|-----------|--------|
| Topic-first | Related = nearest **segment embeddings**, not course day |
| Opt-in | Button only after modal **close** on a seed card |
| One seed | Only the **last closed** card shows the button (simple v1) |
| Preserve search | **Back** restores prior search results + query |
| Same video | Exclude other sections from the **same `video_id`** in v1 |

### 2.3 Engagement definition (v1)

Closing the modal after opening a card counts as engagement. We do **not** require proof the user finished watching. Optional later: YouTube IFrame API `onStateChange` → enable button only after meaningful watch time.

---

## 3. Feature switch (rollout without affecting current capability)

### 3.1 Flag

| Name | Type | Default | Meaning |
|------|------|---------|---------|
| `ENABLE_MORE_LIKE_THIS` | env bool | **`true`** | When false: no UI button, related API returns **404** (or 403) with a clear disabled message |

Parse like existing `SHOW_RESULT_DEBUG` (`1` / `true` / `yes` / `on`).

### 3.2 Exposure to the client

Extend **`GET /api/ui-config`**:

```json
{
  "showResultDebug": true,
  "enableMoreLikeThis": true
}
```

UI reads this on load. If `enableMoreLikeThis` is false:

- Do not show “More like this” after modal close  
- Do not call related API  
- Search, cards, modal behave exactly as today  

### 3.3 Environments

| Environment | Suggested flag |
|-------------|----------------|
| Local default | unset → **true** (opt out with `ENABLE_MORE_LIKE_THIS=false`) |
| Local disable related | `ENABLE_MORE_LIKE_THIS=false` |
| Cloud Run (production) | **`true`** (set in `deploy.sh`) |

`deploy.sh` / `config.env.example` document:

```bash
# ENABLE_MORE_LIKE_THIS=true   # default in deploy
# ENABLE_MORE_LIKE_THIS=false  # to disable
```

To turn off in prod without a code change: set `ENABLE_MORE_LIKE_THIS=false` on Cloud Run env (or redeploy with that value).

### 3.4 Compatibility guarantee

With the flag **off**:

- No new required fields on `/search` responses (optional fields OK if ignored by old UI)  
- No change to card layout for users who never open related  
- Existing mobile/desktop search and playback paths remain valid  

---

## 4. Retrieval design

### 4.1 Core idea

**Do not re-run the user’s text query.**  
Use the **seed timestamp section’s embedding** and query Chroma for nearest neighbors.

Existing helper (to be extended):

```python
recommend_related(section_embedding, top_k=5)
```

### 4.2 Pipeline

```
Seed identity (video_id + timestamp, and/or chroma id)
  → Load seed from Chroma (must be type timestamp_section when possible)
  → Obtain seed embedding
       Prefer: stored embedding from Chroma get/query include embeddings
       Fallback: rebuild embedding_text from metadata + OpenAI embed once
  → Vector query nearest neighbors (n_results >> top_k to allow filtering)
  → Filters:
       - drop seed itself
       - drop rows with same video_id
       - prefer type == timestamp_section (drop video_context)
  → Score: distance → confidence-style score (same family as search if practical)
  → Return top_k cards (same shape as /search video results)
```

### 4.3 Why segment embedding (not original query)

| Approach | Result |
|----------|--------|
| Re-search original query | Same query cloud; not “like *this* clip” |
| Re-embed section text every time | Works; extra OpenAI call |
| Reuse Chroma embedding for seed | True neighborhood of that segment; usually cheaper |

### 4.4 Cost / latency (v1)

| Step | Cost |
|------|------|
| Chroma get + neighbor query | Low |
| Re-embed seed (fallback only) | 1 embedding call |
| Query enrichment + LLM rerank | **Not in v1** |

Much cheaper than full `/search`.

### 4.5 Empty / weak results

If fewer than N usable neighbors after filters, return what we have; UI shows a short empty state if zero (“No similar segments found”).

---

## 5. API design

### 5.1 New endpoint

```http
POST /api/videos/related
Content-Type: application/json
```

**Request:**

```json
{
  "video_id": "1BTlbtXVMRg",
  "timestamp": "12:34",
  "top_k": 5
}
```

Optional alternate: `"id": "{video_id}_{i}_ts"` if search starts returning Chroma ids.

**Success (200):**

```json
{
  "seed": {
    "video_id": "...",
    "timestamp": "12:34",
    "section_title": "...",
    "video_title": "..."
  },
  "results": [ /* same fields as /search video results */ ],
  "count": 5
}
```

**Errors:**

| Code | When |
|------|------|
| 404 | Feature disabled (`ENABLE_MORE_LIKE_THIS=false`) |
| 400 | Missing/invalid seed identity |
| 404 | Seed section not found in Chroma |
| 500 | Unexpected failure |

### 5.2 Search response (optional enhancement)

When flag is on, `/search` **may** include opaque ids to make related lookups robust:

```json
"chroma_id": "VIDEOID_3_ts",
"video_id": "..."
```

Not required for flag-off compatibility. Prefer adding `video_id` if not already present in UI payloads (today UI extracts id from URL).

### 5.3 Auth

Related is **read-only** and public (same as `/search`). Not under `ADMIN_API_KEY`.

---

## 6. UI design (flag-aware)

### 6.1 Mobile / desktop playback

- **Always use in-modal iframe** for video results when implementing this feature (revert mobile “open YouTube app” path as part of this work, or under the same flag if we want a softer cutover).  
- Embed URL: `start={seconds}&playsinline=1`  
- **No autoplay on mobile**; desktop may keep `autoplay=1`  
- Closing modal (`closeModal` / overlay / Escape) marks seed card as eligible for “More like this”

### 6.2 Card state

- After close: attach `data-seed` / JS state for that result index  
- Show **More like this** only if `uiConfig.enableMoreLikeThis === true`  
- Only one card shows the button (last engaged)

### 6.3 Related results view

- Replace `#results` with related cards  
- Banner: `Showing more like: {section_title}`  
- Button: `← Back to Search Result` restores previous results + query text  
- Opening a related card uses the same modal; closing may again offer “More like this” on that new seed (chained exploration OK)

### 6.4 Debug fields

Respect existing `showResultDebug` for timestamp / confidence / hashtags on related cards too.

---

## 7. Implementation plan (phased)

### Phase A — Backend + flag (no UX change when off) ✅

1. Env `ENABLE_MORE_LIKE_THIS` + `/api/ui-config` field ✅  
2. Harden `recommend_related` (resolve seed, filters, scoring, card-shaped output) ✅  
3. `POST /api/videos/related` ✅  
4. Unit/smoke tests with flag on/off ✅ (`scripts/smoke_more_like_this.sh`)  

### Phase B — UI behind flag ✅

1. In-modal playback on mobile (no autoplay) when flag on; soft cutover keeps prior YouTube-open when flag off ✅  
2. Track last-closed seed; show button only if flag on ✅  
3. Related fetch + replace list + Back ✅ 

### Phase C — Dogfood then roll out

1. Enable on staging or temporary prod env  
2. Validate quality of neighbors, latency, OpenAI cost  
3. Set `ENABLE_MORE_LIKE_THIS=true` in default `deploy.sh` when approved ✅  
4. Update TESTING_GUIDE.md  

---

## 8. Testing checklist

**Flag off:**

- [ ] `/api/ui-config` → `enableMoreLikeThis: false`  
- [ ] No “More like this” after watching  
- [ ] `POST /api/videos/related` → 404 (disabled)  
- [ ] Search + modal unchanged  

**Flag on (default / local):**

```bash
# default is on; or force:
ENABLE_MORE_LIKE_THIS=true python api/flask_api_server.py
```

- [ ] Close modal → button on that card  
- [ ] Related results are other videos, topic-similar  
- [ ] Back restores original search  
- [ ] Mobile: modal + manual play  

**Deploy:**

`deploy.sh` sets `ENABLE_MORE_LIKE_THIS=true`. To disable:

```bash
gcloud run services update na21days-media-api \
  --update-env-vars=ENABLE_MORE_LIKE_THIS=false
```

---

## 9. Open decisions (locked for v1 unless changed)

| Topic | v1 decision |
|-------|-------------|
| Trigger | Explicit button after **modal close** |
| List behavior | **Replace** results + **Back** |
| Same video | **Exclude** same `video_id` |
| Rerank | **No** LLM rerank |
| Feature default | **On** (set `ENABLE_MORE_LIKE_THIS=false` to disable) |
| Mobile play | In-modal, **no** required autoplay |

---

## 10. Revision history

- **v0.1** — Initial design: UX flow, segment-embedding retrieval, `ENABLE_MORE_LIKE_THIS` feature switch, API/UI/rollout plan.
- **v0.2** — Phase A implemented: flag in `/api/ui-config`, hardened `recommend_related`, `POST /api/videos/related`, smoke script.
- **v0.3** — Phase B implemented: UI behind flag (More like this after modal close, related list + Back, mobile in-modal when flag on).
- **v0.4** — Rollout: default `ENABLE_MORE_LIKE_THIS=true` in app + `deploy.sh`.

When implementing, bump this section and link PRs/commits here.
