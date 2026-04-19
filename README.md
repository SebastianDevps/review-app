# Review App

AI Code Review — GitHub App + Plane integration + semantic indexer + dashboard.

**What it does:** Dev opens PR → AI reviews with full project context → posts to GitHub → posts to Plane → moves task state. Zero manual friction.

**Stack:** FastAPI · Celery · Redis · PostgreSQL · Claude (Haiku + Sonnet) · ChromaDB · Tree-sitter · Next.js 15

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [How the Review Pipeline Works](#how-the-review-pipeline-works)
3. [How Indexing Works](#how-indexing-works)
4. [Context Strategy](#context-strategy)
5. [Phase 1 — Setup](#phase-1--setup)
6. [Phase 2 — Semantic Indexer](#phase-2--semantic-indexer)
7. [Phase 3 — Dashboard](#phase-3--dashboard)
8. [Configuration Reference](#configuration-reference)
9. [Cost Model](#cost-model)
10. [Running Locally](#running-locally)
11. [Running with Docker](#running-with-docker)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          GitHub App Webhook                         │
│                  (HMAC-SHA256 verified, HTTP 202 immediately)        │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │   FastAPI API   │  port 8000
                    │   app/main.py   │
                    └────────┬────────┘
                             │  enqueue task via Redis
           ┌─────────────────┴──────────────────┐
           │                                    │
  ┌────────▼─────────┐               ┌──────────▼──────────┐
  │  Celery: reviews │               │  Celery: indexing   │
  │  concurrency=4   │               │  concurrency=2      │
  │  queue=reviews   │               │  queue=indexing     │
  └────────┬─────────┘               └──────────┬──────────┘
           │                                    │
  ┌────────▼──────────────────┐     ┌───────────▼───────────────┐
  │   process_pull_request    │     │     index_repository      │
  │                           │     │                           │
  │  1. GitHub: diff+metadata │     │  1. Download tarball      │
  │  2. Plane: ticket context │     │  2. Tree-sitter parse     │
  │  3. ChromaDB+BM25: chunks │     │  3. Upsert ChromaDB       │
  │  4. Haiku: classify       │     │  4. Build BM25 index      │
  │  5. Sonnet: review        │     │  5. Generate project ctx  │
  │  6. Save → PostgreSQL     │     │  6. Mark indexed in DB    │
  │  7. Post → GitHub PR      │     └───────────────────────────┘
  │  8. Post + state → Plane  │
  └───────────────────────────┘
           │
  ┌────────▼─────────────────┐
  │   PostgreSQL + API       │  /api/* — feeds dashboard
  │   Next.js Dashboard      │  port 3000
  └──────────────────────────┘
```

**Two Celery queues** — isolated so slow indexing jobs (minutes) never block fast reviews (30–60s).

---

## How the Review Pipeline Works

### Trigger

A developer opens (or pushes to) a PR. GitHub sends a webhook event to `/webhooks/github`. The signature is verified via `HMAC-SHA256` before any processing. The API returns `HTTP 202` immediately — GitHub expects a response within 10 seconds.

### Task: `process_pull_request`

```
1. Upsert repository + PR records in PostgreSQL
   └── So the dashboard shows the PR immediately, even before the review completes.

2. Fetch PR diff and metadata from GitHub
   └── GitHub API via installation access token (RS256 JWT → 1hr token exchange).
   └── Gate: skip if diff > 3000 lines (configurable via review-config.yml).

3. Fetch Plane ticket context
   └── Extracts issue ID from branch name: feat/PROJ-42-name → sequence ID 42
   └── Fetches title, description, labels, assignees from Plane API.

4. Build semantic context (hybrid BM25 + vector search)
   └── Detailed in "Context Strategy" section below.

5. Haiku classifies the PR
   └── Input: diff summary + context (~350 tokens)
   └── Output: trivial | moderate | complex
   └── Trivial PRs (<50 lines) → auto-approved, no Sonnet call → saves ~97% cost for small PRs.

6. Sonnet reviews (moderate + complex only, ~30% of PRs)
   └── Full diff + semantic context + Plane ticket injected into prompt.
   └── JSON output: classification, approved, summary, issues[], plane_state.
   └── Issues have: severity (critical/high/medium/low), file, line, comment.

7. Save review to PostgreSQL
   └── Review + ReviewIssue records persisted.
   └── Dashboard shows real data immediately after this step.

8. Post GitHub PR comment
   └── Formatted markdown: summary, issues by severity, recommendation.

9. Post Plane comment + transition state
   └── Posts compact review summary to the Plane task.
   └── Moves state: approved → qa_testing, refused → refused, else → code_review.
```

### Retry behavior

Tasks have `max_retries=3` (reviews) and `max_retries=2` (indexing) with exponential backoff. `task_acks_late=True` ensures tasks are not lost if a worker crashes mid-execution.

---

## How Indexing Works

### Trigger

Indexing fires automatically on:
- **Push to default branch** (`push` webhook event) — keeps context fresh after every merge.
- **New installation** — indexes all repos when the GitHub App is installed.
- **Manual trigger** — `POST /index/{installation_id}/{owner}/{repo}`.

### Task: `index_repository`

```
1. Download repo snapshot (tarball, not git clone)
   └── ~5x faster than git clone. No .git directory needed.
   └── Security: strips absolute paths and ".." from tar members before extraction.

2. Parse all source files with Tree-sitter
   └── 16 languages: Python, JS, TS, Go, Rust, Java, Ruby, PHP, C#, C++, C, Kotlin, Swift, Scala.
   └── 44 semantic node types extracted: functions, classes, methods, routes, models, etc.
   └── Each chunk: file_path, language, node_type, name, start_line, end_line, content, tags.
   └── Tags auto-inferred: route, async, auth, database, test, migration, queue, whatsapp.
   └── Fallback chunker for unsupported languages (line-based, 50-line windows).
   └── Skip: node_modules, .git, __pycache__, dist, build, coverage, .venv, vendor.

3. Delete old index, upsert new chunks into ChromaDB
   └── PersistentClient at /data/chromadb.
   └── all-MiniLM-L6-v2 local embeddings (384-dim, free, runs on CPU).
   └── Cosine similarity. Batch upsert in 500-chunk windows.

4. Build BM25 index
   └── BM25Okapi (rank_bm25) over chunk summaries.
   └── Tokenizer: splits camelCase, snake_case, punctuation → lowercase tokens ≥2 chars.
   └── Persisted to /data/bm25/{repo}.pkl — survives worker restarts.
   └── Loaded lazily on first search per worker.

5. Generate project context document
   └── Detailed in "Context Strategy" section below.
   └── Stored at /data/bm25/{repo}_context.md.

6. Mark repository as indexed in PostgreSQL
   └── Updates indexed_at timestamp and chunk_count.
```

---

## Context Strategy

This is the core of review quality. Three layers, injected in this order:

### Layer 1 — Project Context (auto-generated, 2500 chars budget)

Generated once per repo at index time by `app/context_generator.py`. Equivalent to running `mempalace export` locally, but fully automated server-side.

Contains:
- **Architecture overview** — dominant languages, top directories, semantic unit distribution.
- **Entry points & routes** — all `@route`-tagged chunks, async handlers grouped by file.
- **Core domain modules** — files scored by semantic density (unique node types × tag diversity).
- **Data models** — ORM/schema classes (`*Model`, `*Schema`, `*Entity`), database-tagged chunks.
- **Developer rules** — content of `CLAUDE.md` from the repo root, if present (max 2000 chars, highest priority).
- **Test coverage map** — test files and which functions they test.

Stored in `/data/bm25/{repo}_context.md`. Re-generated on every index run.

**Fallback:** If no generated context exists yet (repo not indexed), falls back to reading `CLAUDE.md` directly from the local snapshot.

### Layer 2 — Plane Ticket (dynamic per PR)

Fetched live from the Plane API using the issue sequence ID extracted from the branch name. Contains: title, description, labels, state. Gives the AI the "why" behind the PR — crucial for judging whether the implementation matches the requirement.

### Layer 3 — Hybrid Semantic Search (dynamic per PR, 6000 chars budget)

For each changed file in the diff, finds the most relevant existing code in the repo. Uses **Reciprocal Rank Fusion (RRF)** of two retrieval systems:

```
Vector search (ChromaDB)          BM25 search (rank_bm25)
───────────────────────           ───────────────────────
Semantic similarity               Exact keyword match
"auth logic"                      "send_hsm_template"
"message sending"                 "is_blacklisted"
Finds related patterns            Finds exact identifiers
```

**RRF formula:** `score(chunk) = Σ 1 / (60 + rank_i)` across both result lists.

No score normalization needed — rank-based fusion is immune to scale differences between cosine similarity and BM25 scores. This is the same pattern used by Elasticsearch, Vespa, and Weaviate.

**GitNexus benchmark:** Hybrid recall@5 = 96.6% vs vector-only 89.2%.

Search flow per PR:
1. Extract changed file paths from diff headers.
2. Extract symbol names from `@@` context lines and `+` added lines.
3. For each changed file (up to 5): query with `file:{path} functions: {symbols}` → top 3 chunks.
4. Global symbol query across all files → top 5 additional chunks.
5. Deduplicate by `{file_path}:{start_line}`.
6. Assemble within 6000 char budget, changed files first.

**Result quality example:**
```
Phase 1 (no context):
  "este archivo tiene un posible null pointer en línea 42"

Phase 2 (hybrid context):
  "en WhatsappService.send_message() (línea 42), cuando last_message_at > 24h
   debes usar HSM template — esto viola la regla crítica definida en
   MessageRouter.route_outbound() (encontrado en app/routers/message_router.py:18)"
```

---

## Phase 1 — Setup

### 1. Create a GitHub App

Go to **GitHub → Settings → Developer Settings → GitHub Apps → New GitHub App**.

| Field | Value |
|---|---|
| Name | `Review App` (or any name) |
| Homepage URL | `http://localhost:8000` |
| Webhook URL | `https://your-ngrok-url.ngrok.io/webhooks/github` |
| Webhook secret | Any random string — save it for `.env` |

**Permissions (Repository):**
- Contents: **Read**
- Pull requests: **Read & Write**
- Metadata: **Read**

**Subscribe to events:** `Pull request`, `Push`

After creating: download the **Private Key** (`.pem`), note the **App ID**, install the App on your test repo.

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# GitHub App
GITHUB_APP_ID=12345
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=your-webhook-secret

# AI
ANTHROPIC_API_KEY=sk-ant-...

# Plane
PLANE_API_KEY=plane_api_...
PLANE_WORKSPACE_SLUG=your-workspace
PLANE_PROJECT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Database (for Docker, these are pre-configured)
DATABASE_URL=postgresql://review:review@localhost:5432/review_app
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

### 3. Configure Plane states

Edit `review-config.yml` with your Plane workspace details and state UUIDs.

Get state UUIDs:
```bash
curl -H "x-api-key: YOUR_KEY" \
  "https://api.plane.so/api/v1/workspaces/YOUR_SLUG/projects/YOUR_PROJECT_ID/states/"
```

```yaml
plane:
  workspace_slug: "your-workspace"
  project_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  states:
    code_review: "UUID-of-code-review-state"
    qa_testing:  "UUID-of-qa-testing-state"
    refused:     "UUID-of-refused-state"
```

### 4. Branch naming convention

The app extracts the Plane issue ID from the branch name:

```
feat/NELLUP-42-my-feature    ✅  links to Plane issue #42
fix/PROJ-7-fix-bug            ✅  links to Plane issue #7
NELLUP-100/feature-name       ✅  links to Plane issue #100
my-random-branch              ⚠️  review runs but no Plane update
```

---

## Phase 2 — Semantic Indexer

Phase 2 adds Tree-sitter-based semantic indexing so the AI understands your codebase, not just the diff.

### Manual index trigger

```bash
# Trigger indexing for a specific repo
curl -X POST http://localhost:8000/index/{installation_id}/{owner}/{repo}

# Check indexing status
curl http://localhost:8000/index/status/{task_id}

# List all indexed repos with chunk counts
curl http://localhost:8000/repos
```

### Auto-reindex on push

Every push to the default branch (`main`/`master`) automatically triggers a background re-index. The review pipeline always uses the latest indexed state.

### CLAUDE.md (developer rules)

Add a `CLAUDE.md` to any connected repo root. It will be read during indexing and injected into every review prompt as highest-priority context.

```markdown
# My Project — Review Context

## Critical rules
- Never store passwords in plaintext
- All API endpoints require JWT authentication
- Database queries must use parameterized statements
- WhatsApp messages > 24h window require HSM template

## Architecture
- Backend: FastAPI + PostgreSQL
- Auth: JWT, 1h expiry, refresh tokens in Redis
- Message routing: app/routers/message_router.py
```

---

## Phase 3 — Dashboard

The dashboard at `http://localhost:3000` provides:

| Page | URL | What it shows |
|---|---|---|
| Overview | `/dashboard` | Total PRs, approval rate, issues by severity, active repos |
| Repos | `/repos` | All connected repos with index status and chunk counts |
| Repo detail | `/repos/{owner}/{repo}` | PR list with review outcomes for a specific repo |
| Review detail | `/reviews/{id}` | Full review: summary, issues table, diff context used |
| Developers | `/devs` | Per-developer metrics: approval rate, issue patterns |
| Dev detail | `/devs/{login}` | Individual developer review history and trend |

### Dashboard API endpoints

All served from FastAPI under `/api/*`:

```
GET  /api/stats              — global counts and rates
GET  /api/repos              — repos with index status
GET  /api/repos/{owner}/{repo}/prs  — PR list for a repo
GET  /api/reviews/{id}       — full review detail
GET  /api/devs               — developer leaderboard
GET  /api/devs/{login}       — individual developer stats
```

---

## Configuration Reference

### `review-config.yml`

```yaml
thresholds:
  trivial_max_lines: 50        # PRs under this → auto-approve, Haiku only
  max_diff_lines: 3000         # PRs over this → skip entirely

models:
  classify: "claude-haiku-4-5-20251001"   # always runs (~$0.003/PR)
  review:   "claude-sonnet-4-6"           # only for moderate/complex (~$0.07/PR)

plane:
  workspace_slug: "your-workspace"
  project_id:     "UUID"
  states:
    code_review:  "UUID"       # default state when review runs but not approved/refused
    qa_testing:   "UUID"       # state when PR is approved
    refused:      "UUID"       # state when PR is refused

review:
  inject_claude_md:        true    # inject CLAUDE.md into review prompt
  auto_transition_state:   true    # move Plane state automatically
  post_github_comment:     true    # post review comment on the PR
  post_plane_comment:      true    # post summary comment on Plane task
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_APP_ID` | ✅ | GitHub App numeric ID |
| `GITHUB_APP_PRIVATE_KEY` | ✅ | RS256 private key (PEM format, `\n`-escaped) |
| `GITHUB_WEBHOOK_SECRET` | ✅ | Webhook signature secret |
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `PLANE_API_KEY` | ✅ | Plane personal API token |
| `PLANE_WORKSPACE_SLUG` | ✅ | Plane workspace slug |
| `PLANE_PROJECT_ID` | ✅ | Plane project UUID |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `CELERY_BROKER_URL` | ✅ | Redis URL for Celery broker |
| `CELERY_RESULT_BACKEND` | ✅ | Redis URL for Celery results |
| `LOG_LEVEL` | — | Default: `INFO` |

---

## Cost Model

### Per-PR cost breakdown

| PR type | % of volume | Models used | Cost/PR |
|---|---|---|---|
| Trivial (< 50 lines) | ~30% | Haiku only | ~$0.003 |
| Moderate | ~40% | Haiku + Sonnet | ~$0.073 |
| Complex | ~30% | Haiku + Sonnet | ~$0.073 |

### Monthly cost by volume

| Volume | Monthly cost |
|---|---|
| 100 PRs/month | ~$2.40 |
| 500 PRs/month | ~$12 |
| 1 000 PRs/month | ~$24 |
| 5 000 PRs/month | ~$120 |

Cost is dominated by Sonnet reviews (~$0.07/PR for complex PRs). The Haiku classification gate keeps costs low by routing trivial PRs to Haiku-only ($0.003).

ChromaDB embeddings use `all-MiniLM-L6-v2` locally — **zero embedding cost**.

---

## Running Locally

### Prerequisites

- Python 3.11+
- Redis
- PostgreSQL
- `ngrok` (for webhook delivery)

```bash
# Install dependencies
pip install -e ".[dev]"

# Initialize database
python -c "from app.database import init_db; init_db()"

# Start Redis
redis-server &

# Start Celery workers (two separate queues)
celery -A app.worker worker --queues=reviews --concurrency=4 --loglevel=info &
celery -A app.worker worker --queues=indexing --concurrency=2 --loglevel=info &

# Start Celery beat (cron jobs)
celery -A app.worker beat --loglevel=info &

# Start API
uvicorn app.main:app --reload --port 8000

# Expose webhook
ngrok http 8000
# → copy the https URL to your GitHub App webhook settings
```

---

## Running with Docker

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your values

# Start all services
docker-compose up

# Services:
#   api            → http://localhost:8000
#   dashboard      → http://localhost:3000
#   worker-reviews → Celery queue: reviews
#   worker-indexing → Celery queue: indexing
#   beat           → Celery beat (cron: cleanup stale snapshots every 24h)
#   postgres       → port 5432
#   redis          → port 6379
```

### Service architecture

```yaml
services:
  api:             FastAPI — webhook receiver + REST API
  worker-reviews:  Celery — processes PR reviews (concurrency=4)
  worker-indexing: Celery — indexes repositories (concurrency=2)
  beat:            Celery beat — cleanup cron job
  dashboard:       Next.js 15 — metrics dashboard
  postgres:        PostgreSQL 16 — reviews, PRs, repos
  redis:           Redis 7 — Celery broker + result backend
```

### Volumes

| Volume | Purpose |
|---|---|
| `postgres_data` | PostgreSQL data |
| `chromadb_data` | ChromaDB vector store (`/data/chromadb`) |
| `bm25_data` | BM25 indexes + project context files (`/data/bm25`) |
| `repos_tmp` | Temporary repo tarballs during indexing (`/data/repos`) |

---

## Project Structure

```
review-app/
├── app/
│   ├── main.py              # FastAPI app — webhook receiver, /api mounts, CORS
│   ├── worker.py            # Celery tasks: process_pull_request, index_repository
│   ├── config.py            # Pydantic settings (env vars)
│   ├── models.py            # SQLAlchemy ORM: Repository, PullRequest, Review, ReviewIssue
│   ├── database.py          # Engine + SessionLocal + init_db()
│   ├── persistence.py       # DB write helpers: upsert_repository, save_review, etc.
│   ├── api.py               # REST API router: /api/stats, /api/repos, /api/devs, etc.
│   ├── github_auth.py       # GitHub App JWT auth, diff/metadata fetch, PR comment post
│   ├── plane_client.py      # Plane API: fetch ticket, post comment, transition state
│   ├── review_engine.py     # Haiku classify + Sonnet review, JSON output, comment format
│   ├── review_config.py     # Load review-config.yml
│   ├── indexer.py           # Tree-sitter parser → SemanticChunk list
│   ├── repo_cloner.py       # GitHub tarball download + extraction
│   ├── context_store.py     # ChromaDB + BM25 hybrid search (RRF), project context store
│   ├── context_builder.py   # Assembles review context: project ctx + ticket + chunks
│   └── context_generator.py # Generates project context markdown from indexed chunks
├── dashboard/               # Next.js 15 App Router dashboard
│   ├── src/app/             # Pages: dashboard, repos, reviews, devs
│   ├── src/components/      # Sidebar, Badge
│   └── src/lib/             # api.ts (typed fetch), auth.ts (NextAuth)
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── review-config.yml
```

---

## License

MIT
