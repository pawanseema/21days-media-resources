# GCP operations scripts

Idempotent scripts for Cloud Run infrastructure and deployments.  
Config lives in `config.env` (gitignored); copy from `config.env.example`.

## Naming convention

GCP resource names use the **`na21days-media-*`** prefix (must start with a letter for Artifact Registry).

| Resource | Name |
|----------|------|
| GCP project ID | `days-search-app` (existing project; do not rename) |
| Artifact Registry repo | `na21days-media` |
| Cloud Run service | `na21days-media-api` |
| GCS bucket | `na21days-media-chroma-days-search-app` |
| Runtime service account | `na21days-media-run` |
| Local Docker image tag | `na21days-media-api:local` |
| Image path | `us-central1-docker.pkg.dev/days-search-app/na21days-media/na21days-media-api:TAG` |

Repo folder / Git remote stay `21days-media-resources` (filesystem + GitHub); that is separate from GCP resource IDs.

## Quick start (first time)

```bash
cd /Users/pawansaxena/playpen/21days-media-resources
cp scripts/gcp/config.env.example scripts/gcp/config.env
# edit config.env if needed

gcloud auth login
gcloud config set project days-search-app

chmod +x scripts/gcp/*.sh
./scripts/gcp/bootstrap.sh
./scripts/gcp/verify.sh
./scripts/gcp/deploy.sh
./scripts/gcp/verify.sh --smoke
```

## Scripts

| Script | When to run |
|--------|-------------|
| `bootstrap.sh` | **Once per GCP project/env**, or after adding new infra. Creates APIs, Artifact Registry, GCS bucket, runtime SA, IAM, secrets, uploads Chroma. |
| `upload-chroma.sh` | After **local ingestion** changes (new videos indexed locally). |
| `deploy.sh` | Every **release** (app/code change). Builds image + deploys Cloud Run revision. |
| `verify.sh` | After bootstrap or deploy; `--smoke` curls `/health`. |

## Long-term workflow

```
Local dev (venv + Flask/Gunicorn/Docker)
        │
        ▼
Playlist / video ingestion CLI  ──►  resources/chroma_free_store/
        │
        ▼
upload-chroma.sh  ──►  GCS bucket
        │
        ▼
deploy.sh  ──►  Artifact Registry image  ──►  Cloud Run revision
```

### Typical cadence

| Change type | What to run |
|-------------|-------------|
| New app code only | `./scripts/gcp/deploy.sh` |
| Chroma data updated locally | `./scripts/gcp/upload-chroma.sh` then `./scripts/gcp/deploy.sh` (deploy optional if only data changed) |
| Rotated API keys | Update local `*_api_key.txt`, then `./scripts/gcp/bootstrap.sh --update-secrets --skip-chroma-upload` then `./scripts/gcp/deploy.sh --no-build` |
| Rotated admin key | `./scripts/gcp/bootstrap.sh --update-secrets --skip-chroma-upload` (rotates admin secret only when combined with file updates; admin always rotates on `--update-secrets`) |
| New GCP environment (staging) | Copy `config.env` → `config.staging.env`, change `SERVICE`/`BUCKET`, run `bootstrap.sh` with `CONFIG_FILE=...` *(future: add env flag)* |
| Rollback bad deploy | `gcloud run services update-traffic SERVICE --to-revisions=PREVIOUS=100` |

### Keeping GCP in sync with development

1. **Infrastructure** (`bootstrap.sh`) — run rarely; safe to re-run (idempotent).
2. **Data** (`upload-chroma.sh`) — run when local Chroma changes; does not rebuild the app.
3. **Application** (`deploy.sh`) — run on every release; tags images with git SHA by default.
4. **Verification** (`verify.sh`) — run after any of the above.

### Image tags

`deploy.sh` defaults to `git rev-parse --short HEAD`. Override:

```bash
./scripts/gcp/deploy.sh --tag v1.2.0
```

### Redeploy without rebuild

```bash
./scripts/gcp/deploy.sh --tag v1.2.0 --no-build
```

## bootstrap.sh options

```bash
./scripts/gcp/bootstrap.sh                      # full Phase 2 + Chroma upload
./scripts/gcp/bootstrap.sh --skip-chroma-upload   # infra + secrets only
./scripts/gcp/bootstrap.sh --update-secrets     # rotate secrets from local key files
```

## Prerequisites

- `gcloud` CLI authenticated
- Billing enabled on the GCP project
- Local venv with deps (for `browse_videos.py stats` before upload)
- `api_key.txt` and `openai_api_key.txt` at project root (first bootstrap only)

## Related docs

- [CLOUD_RUN_DEPLOYMENT_PLAN.md](../../docs/CLOUD_RUN_DEPLOYMENT_PLAN.md)
- [TESTING_GUIDE.md](../../TESTING_GUIDE.md)
