# Google Cloud Run deployment plan

This document is a **review-first plan** for packaging the Flask app for Cloud Run. It does **not** assume implementation is complete until you approve iterations.

## Environment (this machine)

| Item | Value |
|------|--------|
| **Project root** | `/Users/pawansaxena/playpen/21days-media-resources` |
| **Git remote** | `https://github.com/pawanseema/21days-media-resources.git` |
| **Local Python** | 3.9.6 (dev); container image targets **3.11-slim** |
| **Local API** | `python3 api/flask_api_server.py` → `http://127.0.0.1:5005` |
| **Local Chroma** | `resources/chroma_free_store/` (653+ entries as of last verify) |
| **Local secrets** | `api_key.txt`, `openai_api_key.txt` at project root (gitignored) |
| **Local sanity check** | `./quick_test.sh` from project root |
| **Related docs** | [DESIGN.md](../DESIGN.md), [TESTING_GUIDE.md](../TESTING_GUIDE.md) |

**Cloud naming (suggested):** service `na21days-media-api`, Artifact Registry repo `na21days-media`, GCS bucket `na21days-media-chroma-days-search-app` (Artifact Registry names cannot start with a digit).

## Goals

1. **Preserve all existing HTTP APIs** — same paths, methods, request/response shapes, and handler logic (e.g. `/search`, `/health`, `/api/resources/*`, `/api/videos/ingest`, static `/` and `/ui/*`). Only **wiring** (process model, env, paths) changes where required for Cloud Run.
2. **Durable ChromaDB** — use a fixed container path (e.g. `/mnt/chroma_data`) backed by a **Cloud Storage bucket** mounted as a Cloud Run volume so data survives restarts and scale-to-zero.
3. **Concurrency** — Gunicorn with **`gthread`**, **1 worker**, **8 threads**, and **`--timeout 0`** so long ingest/search calls are not killed by Gunicorn while Cloud Run still enforces its own request limits where applicable.
4. **Cost** — **`--min-instances 0`**, small image base **`python:3.11-slim`**.
5. **Secrets** — keys loaded via **`os.getenv`** (with clear env var names); local `.env` parity documented; **Secret Manager** as the source of truth in GCP.

---

## Deployment modes: production vs local (explicit choice)

We support **two first-class ways** to run the same application; you pick which applies for a given session or environment. **No route renames** — only configuration and process model differ.

| Mode | Purpose | Typical stack | Chroma / data | Secrets |
|------|---------|---------------|---------------|---------|
| **Local (laptop)** | Development, testing, validation before or after prod changes | `cd /Users/pawansaxena/playpen/21days-media-resources` then `python3 api/flask_api_server.py` (or local Gunicorn mirroring prod); validate with `./quick_test.sh` | **`resources/chroma_free_store/`** (or override with **`CHROMA_PERSIST_DIR`**) | `api_key.txt` / `openai_api_key.txt` and/or **`.env`** (not committed) |
| **Production** | Live users on the internet | **Cloud Run** + Gunicorn (`gthread`) | **`CHROMA_PERSIST_DIR=/mnt/chroma_data`** backed by **GCS volume mount** | **Secret Manager** → env vars at deploy time |

**How to “pick” the mode (planned):**

- Single codebase; **environment variables** (and optional small wrapper scripts or `Makefile` targets) decide behavior — e.g. `DEPLOY_ENV=local|production` or simply “absence of Cloud Run + presence of local paths”.
- **Local** workflow: clone to `/Users/pawansaxena/playpen/21days-media-resources`, add keys, `pip3 install -r requirements.txt`, run `./quick_test.sh`, then `python3 api/flask_api_server.py` → `http://127.0.0.1:5005`.
- **Production** workflow: build/push image, `gcloud run deploy` with volume + secrets + limits documented elsewhere in this plan.

**Testing parity:** where possible, run **the same Gunicorn command** locally in a container (`docker run …`) before promoting an image to Cloud Run, so thread/worker behavior matches prod.

---

## Production upgrades: new capabilities and bug fixes

**Goal:** predictable, reversible rollouts without surprising users or corrupting Chroma.

1. **Version control** — every production deploy ties to a **git commit** (and optionally **semver tags** on releases).
2. **Immutable artifacts** — build a **new container image** per release (`:TAG` or digest); never “SSH and edit” a running revision.
3. **Deploy flow** — push image to **Artifact Registry** → `gcloud run deploy` (new **revision**) → Cloud Run shifts traffic to the new revision by default; keep the previous revision available for instant rollback.
4. **Rollback** — revert traffic to the **previous revision** in Cloud Run (Console or `gcloud run services update-traffic`) if a regression appears; no need to rebuild immediately.
5. **Data safety** — risky schema or Chroma layout changes: **backup the GCS bucket** (or export) before migrations; document one-off migration steps in the release notes when needed.
6. **Optional staging** — a second Cloud Run service (e.g. `na21days-media-api-staging`) with its own bucket or bucket prefix and secrets, for smoke tests against production-like data **without** pointing real users at it until you promote the same image to prod.

**Bugfix vs feature:** same pipeline; smaller images and faster deploys help; consider **CHANGELOG.md** or GitHub Releases for auditability.

---

## Cost sensitivity and protection from abusive traffic

Public **search** endpoints call **OpenAI** (embeddings + enrichment + rerank per request), so **unbounded traffic can generate large bills**. Plan defense in layers.

### GCP platform controls (recommended baseline)

- **Billing budgets + alerts** — mandatory: email/SMS when spend or forecast crosses thresholds.
- **Cloud Run `max-instances`** — cap burst scale (e.g. low single digits for a small app) so runaway traffic cannot spawn unlimited instances.
- **Concurrency** — tune per instance so each instance does not accept unbounded parallel expensive work (aligns with Gunicorn thread count).
- **Request timeout** — keep search paths within a reasonable ceiling (longer only for admin ingest if exposed over HTTP).

### Edge protection (stronger, optional)

- **Google Cloud Armor** (typically in front of **HTTPS Load Balancing** + Cloud Run backend) — rate limits, geo restrictions, IP allow/deny lists, bot management (evaluate cost vs benefit; Armor has its own pricing).
- **API Gateway / Apigee** — alternative for quotas and keys; more setup than Armor for a small app.

### Application-level controls (always worth doing)

- **Rate limiting** on **`POST /search`** and **`POST /api/resources/search`** (e.g. per client IP + sliding window).  
  - **Note:** in-memory limiters are **per instance**; with multiple Cloud Run instances, effective limit = limit × instances unless you use a **shared store** (Redis/Memorystore) or edge Armor.
- **Payload limits** — reject oversized JSON bodies and absurd `top_k`.
- **Logging / metrics** — log 429s and unusual QPS; optional simple anomaly alert (Log-based metric).

### Product-level levers

- If abuse continues: require a **static site API key** header for mobile/public search (still “public” in the sense that the key ships in the app, but **rotatable** and **throttleable**), or move search behind signed short-lived tokens.

Document chosen limits in **`TESTING_GUIDE.md`** / runbook after implementation.

---

## Access model (agreed direction)

### Principles

1. **Video and resource management** (ingest, update, delete, re-process) is **strictly admin-only** — performed by you (or trusted automation), not by end users and **not** by any future mobile app.
2. **End users** interact through the **HTML frontend in a browser** for day-to-day discovery.
3. **A future mobile app** is treated as **another read-only client**: it may call the same **search** APIs as the web UI (`POST /search`, `POST /api/resources/search`). It must **not** ship admin credentials or expose management flows; **no** mobile path for ingest, video ingest, resource CRUD, or Chroma deletes.

**Bulk playlist ingestion** remains an **admin CLI** task: `resources/video_processing.py` on your machine or a trusted runner (YouTube + OpenAI keys there). Optional `POST /api/videos/ingest` exists for targeted re-adds but stays **admin-protected** like other mutators, not a public mobile surface.

### Route matrix

| Surface | Who | Intended exposure | Notes |
|--------|-----|-------------------|--------|
| **`/`**, **`/ui/*`**, resource HTML routes | End users | **Public** (typical) | Browser UI; calls search JSON below. |
| **`POST /search`** | Web UI + **future mobile (read-only)** | **Public** | Read-only semantic search over indexed videos; same contract for web and mobile clients. |
| **`POST /api/resources/search`** | Same | **Public** | Read-only search over handouts/resources. |
| **`GET /health`** | Ops / probes | **Public** or restricted | Often left public for uptime checks; can be locked if you prefer. |
| **`POST /api/resources/ingest`**, **`PUT /api/resources/<id>`**, **`POST /api/videos/ingest`** | **Admin only** | **Private** | Must not be callable without admin auth; **never** intended for mobile or anonymous clients. |
| **Playlist pipeline** | **Admin** | **Not HTTP** | `python3 resources/video_processing.py` (or `--video-id …`); primary way new playlist videos enter the DB. |
| **`browse_videos.py` delete-video** (CLI) | **Admin** | **Not Cloud Run** | Trusted environment only; any future HTTP delete would match **private** mutator policy. |

**Important Cloud Run constraint:** IAM “authenticated invokers only” applies **per service** (or per separate service), not per URL path. So “public search + private mutators” on **one** Cloud Run service requires one of:

1. **Application-level protection (recommended for a single service)** — e.g. Flask `before_request` or decorators: require a shared secret header (`X-Admin-Key`), signed JWT, or Firebase Auth **only** on `POST/PUT` mutating routes; leave `GET` UI and `POST` search open.  
2. **Two services** — e.g. `na21days-media-web` (public: static + `/search` + `/api/resources/search`) and `na21days-media-admin` (private IAM + ingest/update/video-ingest), sharing the same Chroma bucket mount (or admin writes rarely via CLI only).  
3. **Identity-Aware Proxy (IAP)** in front of the whole service, then path-based or audience rules (more moving parts).

**Plan default for implementation:** single service + **(1)** unless you prefer splitting services.

Update the example `gcloud` snippet later to match the chosen model (e.g. `--no-allow-unauthenticated` only if **every** route is behind IAP or all clients send Google ID tokens; otherwise public invoker + app-level admin secret for mutations).

---

## Current codebase touchpoints (for the implementation phase)

| Area | Today | Planned change |
|------|--------|------------------|
| Chroma path | `config.get_chroma_dir()` → `resources/chroma_free_store` under project root | Prefer **`CHROMA_PERSIST_DIR`** env var defaulting to **`/mnt/chroma_data`** in container; keep local default as today **or** override via `.env` for dev parity. |
| YouTube key | `config.load_yt_api_key()` reads `api_key.txt` | **`YOUTUBE_API_KEY`** (or `GOOGLE_API_KEY`) via `os.getenv`, optional file fallback only for local dev if you want zero breaking change. |
| OpenAI key | `config.load_openai_api_key()` reads `openai_api_key.txt` | **`OPENAI_API_KEY`** via `os.getenv`, optional file fallback for local. |
| App entry | `python3 api/flask_api_server.py` on port **5005** (Flask dev server) | **Gunicorn** module target `api.flask_api_server:app` on **`PORT`** (default **8080** in Cloud Run); production only in container. |
| Container artifacts | None (`Dockerfile`, `gunicorn.conf.py`, `.dockerignore` not yet added) | Add as part of implementation checklist below. |

**API preservation:** No renaming of routes or changing JSON contracts in Flask handlers; only `config`/startup and process supervisor change.

---

## Container layout (planned)

```
/app/                          ← WORKDIR (COPY from repo root)
├── api/
├── config.py
├── resources/
├── search/
├── ui/
├── requirements.txt
└── …
/mnt/chroma_data               ← Cloud Run volume mount (GCS-backed)
```

Local repo layout for reference: see [DESIGN.md](../DESIGN.md#repository-layout).

- Image: **`python:3.11-slim`**
- Install deps from `requirements.txt` (pin versions in a follow-up if desired).
- **`WORKDIR`** at project root (or `/app`) consistent with imports (`config`, `search`, `resources`, `api`).

### Gunicorn (planned)

- **`gunicorn.conf.py`**: `worker_class = "gthread"`, `workers = 1`, `threads = 8`, sensible `bind = "0.0.0.0:8080"` (Cloud Run sets `PORT`; bind should use `os.environ.get("PORT", "8080")` in command or config).
- **Command**: include **`--timeout 0`** as requested (worker will not enforce a hard Gunicorn worker timeout). Note: Cloud Run still has **request timeout** (configurable up to 60 minutes on 2nd gen); set that in deploy for long ingest.

---

## Persistent storage: Chroma + GCS volume mount

### Behavior

- Cloud Run mounts the bucket at **`/mnt/chroma_data`** (example).
- Chroma **`PersistentClient(path=...)`** uses that directory; SQLite and index files live under it and are persisted as objects in the bucket (via GCS FUSE semantics).

### IAM (runtime service account)

Grant the Cloud Run **service identity** (default compute SA or a dedicated SA) on the bucket:

- **Read + write** for ingestion: e.g. **`roles/storage.objectUser`** on the bucket (or tighter custom role).  
- Read-only is **not** enough if you ingest/delete from the app.

### Operational caveats (document now, tune later)

- GCS FUSE is **not fully POSIX**; concurrent writers to the same file can race (“last write wins”). Plan for **single-writer** bursts or accept risk at low scale.
- **Memory**: streaming writes can use significant RAM per open write (Google documents ~64 MiB per streaming write path in some cases). If ingest is heavy, raise Cloud Run **memory** and monitor OOM.
- **Latency**: first access / cold starts may be slower than local disk; acceptable for many search-heavy workloads.

### Seed Chroma data from this machine (before first deploy)

Upload the existing local store to GCS so Cloud Run starts with indexed data:

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

export PROJECT_ID="your-gcp-project"
export BUCKET="na21days-media-chroma-days-search-app"

# Create bucket once (pick region to match Cloud Run, e.g. us-central1)
gcloud storage buckets create "gs://${BUCKET}" --project="${PROJECT_ID}" --location=us-central1

# Copy local Chroma files (run from project root)
gcloud storage cp -r resources/chroma_free_store/* "gs://${BUCKET}/"
```

After deploy, mount the bucket at `/mnt/chroma_data` with `CHROMA_PERSIST_DIR=/mnt/chroma_data`.

**Alternative:** re-run `python3 resources/video_processing.py` from a trusted admin machine after deploy (slower; uses YouTube + OpenAI quotas).

### Example deploy command (illustrative — verify against current `gcloud`)

Per [Cloud Run Cloud Storage volume mounts](https://cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts), deploy can combine volume definition with mount path in one `--add-volume` flag (syntax evolves; confirm with `gcloud beta run deploy --help` on your SDK):

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

export PROJECT_ID="your-gcp-project"
export REGION="us-central1"
export SERVICE="na21days-media-api"
export REPO="na21days-media"
export TAG="v1"   # tie to git commit or semver
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:${TAG}"
export BUCKET="na21days-media-chroma-days-search-app"

# Build and push (from repo root; requires Dockerfile — not yet in repo)
gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}"

gcloud beta run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --platform=managed \
  --execution-environment=gen2 \
  --min-instances=0 \
  --max-instances=3 \
  --memory=1Gi \
  --timeout=300 \
  --allow-unauthenticated \
  --set-env-vars="CHROMA_PERSIST_DIR=/mnt/chroma_data" \
  --set-secrets="OPENAI_API_KEY=openai-api-key:latest,YOUTUBE_API_KEY=youtube-api-key:latest" \
  --add-volume="mount-path=/mnt/chroma_data,type=cloud-storage,bucket=${BUCKET},readonly=false"
```

**Notes:**

- Use **`gcloud beta`** if your SDK requires it for `type=cloud-storage` volumes.
- **`readonly=false`** is required for Chroma writes.
- Add **Secret Manager** env bindings separately (next section).
- **`--allow-unauthenticated`**: aligns with **public search + browser UI** above; pair with **app-level admin auth** on mutating routes (see [Access model](#access-model-agreed-direction)) so ingest/update APIs are not actually open to the world.

---

## Secrets: `.env` → Secret Manager

### Planned env vars (names are illustrative — finalize in implementation)

| Purpose | Suggested env var |
|---------|-------------------|
| OpenAI | `OPENAI_API_KEY` |
| YouTube Data API | `YOUTUBE_API_KEY` |
| Chroma directory | `CHROMA_PERSIST_DIR` |

### Secret Manager steps (high level)

1. Enable **Secret Manager API** on the project.
2. Create secrets, e.g.  
   `gcloud secrets create openai-api-key --data-file=- <<<"sk-..."`  
   (or use Console).
3. Grant the Cloud Run service account **`roles/secretmanager.secretAccessor`** on each secret.
4. Mount secrets as env vars at deploy time, e.g.:  
   `--set-secrets=OPENAI_API_KEY=openai-api-key:latest,YOUTUBE_API_KEY=youtube-api-key:latest`  
   (exact flag syntax: `gcloud run deploy --help` / `--set-secrets`).

### Local development

```bash
cd /Users/pawansaxena/playpen/21days-media-resources

# Keys at project root (current approach)
echo "YOUR_YOUTUBE_API_KEY" > api_key.txt
echo "YOUR_OPENAI_API_KEY" > openai_api_key.txt

pip3 install -r requirements.txt
./quick_test.sh
python3 api/flask_api_server.py   # http://127.0.0.1:5005
```

- Optional: **`.env`** + `python-dotenv` for local runs once `config.py` supports `os.getenv`.
- Never commit `.env` or `*_api_key.txt`; `.gitignore` already excludes them.

---

## GCP account and project setup (what you need)

### Account / billing

- A **Google account** with access to **Google Cloud Console**.
- A **GCP project** (create new or use existing).
- **Billing enabled** on the project (Cloud Run, Artifact Registry, Cloud Storage, Secret Manager are billable; free tiers may apply).

### APIs to enable (typical)

- **Cloud Run API**
- **Artifact Registry API** (recommended for container images)
- **Cloud Build API** (optional, if you build images in GCP)
- **Cloud Storage API**
- **Secret Manager API**
- **YouTube Data API v3** is a **Google Cloud / API Console** product for the *key*, not necessarily a “enable on GCP project” for Cloud Run itself — but your **API key** is created in Google Cloud Console → Credentials.

### IAM roles (typical)

- **You (human deployer)**: e.g. `roles/run.admin`, `roles/artifactregistry.writer`, `roles/storage.admin` (or narrower), `roles/secretmanager.admin` (or narrower), `roles/iam.serviceAccountUser` on the runtime SA.
- **Cloud Run runtime service account**:  
  - Secret accessor on secrets.  
  - Storage object user (or viewer + creator as appropriate) on the Chroma bucket.

### Container registry

- Create an **Artifact Registry** Docker repository in the same **region** as Cloud Run (reduces latency/cross-region issues), e.g.:

```bash
export PROJECT_ID="your-gcp-project"
export REGION="us-central1"

gcloud artifacts repositories create na21days-media \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT_ID}"
```

- Image path pattern: `${REGION}-docker.pkg.dev/${PROJECT_ID}/na21days-media/na21days-media-api:TAG`

### Domain (optional)

- **Not required** for first deploy: Cloud Run gives `https://SERVICE-XXXX.a.run.app`.
- **Custom domain** (optional): verify domain in Cloud Run → Domain mappings; DNS at your registrar (A/AAAA or CNAME per Google instructions). Consider **Cloud Load Balancing** + managed certs for more complex setups.

### Quotas / limits to decide early

- **Cloud Run max instances**, **CPU**, **memory**, **concurrency** (requests per instance). With **8 threads**, you may set concurrency ~8–10 or tune based on workload (I/O-bound vs CPU-bound).
- **Request timeout** for long ingestion (up to service max for gen2).

---

## Open decisions (for you to confirm before implementation)

1. **Auth mechanism for mutating HTTP routes** — resolved at product level: **read-only search stays public** (web + future mobile); **all management** (ingest/update/video-ingest) **admin-only**. Still choose implementation: **shared admin secret header**, **JWT**, **second private Cloud Run service**, or **IAP** (see [Access model](#access-model-agreed-direction)).
2. **Chroma on GCS FUSE** vs **ephemeral disk + periodic export** — FUSE is simpler for “one directory”; export is an alternative if FUSE proves unstable under your write pattern.
3. **CORS** — if a browser UI calls Cloud Run from another origin, you may need Flask-CORS or proxy; out of scope unless you use cross-origin calls.
4. **Audit CSV** (`sahajyoga_recent5_audit.csv`) — today written under project root; Cloud Run filesystem is ephemeral except mounts. Plan: write CSV **inside** `/mnt/chroma_data/...` or disable CSV in cloud / log to Cloud Logging only.
5. **Abuse protection depth** — app-only rate limits vs **Cloud Armor** + LB vs **API key** for public search; pick based on budget and tolerance for ops complexity.

---

## Implementation checklist (after plan approval)

**Automated scripts** live in [`scripts/gcp/`](../scripts/gcp/README.md):

| Script | Purpose |
|--------|---------|
| `bootstrap.sh` | Phase 2 infra (idempotent) + optional Chroma upload |
| `upload-chroma.sh` | Sync local Chroma → GCS after ingestion |
| `deploy.sh` | Build image + deploy Cloud Run revision |
| `verify.sh` | Check infra; `--smoke` hits `/health`, `/api/ui-config`, `/api/videos/related` |

```bash
cp scripts/gcp/config.env.example scripts/gcp/config.env
./scripts/gcp/bootstrap.sh
./scripts/gcp/verify.sh
./scripts/gcp/deploy.sh
```

Manual checklist (if not using scripts):

Run from `/Users/pawansaxena/playpen/21days-media-resources`:

1. Add **`Dockerfile`** + **`gunicorn.conf.py`** + **`.dockerignore`** at project root. ✅
2. Update **`config.py`** to **`os.getenv`** (`OPENAI_API_KEY`, `YOUTUBE_API_KEY`, `CHROMA_PERSIST_DIR`) with file fallback for local dev. ✅
3. Ensure all modules use **`get_chroma_dir()`** only (already true for `video_processing.py`, `resource_ingestion.py`, `browse_*.py`, `search/*.py`). ✅
4. Add **admin auth** on mutating HTTP routes (`X-Admin-Key` or chosen mechanism). ✅
5. **Test container locally** before GCP (see `TESTING_GUIDE.md`). 
6. **GCP:** `./scripts/gcp/bootstrap.sh` (or manual bucket/secrets steps).
7. **Smoke test prod:** `./scripts/gcp/deploy.sh` then `./scripts/gcp/verify.sh --smoke`.
8. Update **`TESTING_GUIDE.md`** with Cloud Run URLs and deploy runbook notes.

---

## Revision history

- **v0.1** — Initial plan for review (routes preserved; Chroma `/mnt/chroma_data`; Gunicorn gthread; Secret Manager; GCP prerequisites).
- **v0.2** — Access model: public UI + public search JSON for future clients; mutating APIs private; playlist ingestion remains CLI-first (`video_processing.py`).
- **v0.3** — Clarified: future **mobile app = read-only search only**; **video/resource management never** exposed to mobile or anonymous callers (admin CLI + protected mutators only).
- **v0.4** — Added: **local vs production** deployment modes (explicit choice); **production upgrade/rollback** strategy; **cost and abuse protection** (budgets, `max-instances`, rate limits, optional Cloud Armor).
- **v0.5** — Aligned with **this environment**: project root `/Users/pawansaxena/playpen/21days-media-resources`, GitHub remote, `na21days-media-*` GCP naming (Artifact Registry names cannot start with a digit), local verify via `./quick_test.sh`, Chroma seed upload from `resources/chroma_free_store/`, expanded deploy/build checklist.

When you approve or mark changes, we will bump this section and implement accordingly.
