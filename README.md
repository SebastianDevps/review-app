# Review App

AI Code Review — GitHub App + Plane integration.

Opens a PR → AI reviews it → comments on GitHub → comments on Plane → moves the task state. Zero manual friction.

**Stack:** FastAPI · Celery · Redis · PostgreSQL · Claude (Haiku + Sonnet) · Plane API

---

## How it works

```
Dev opens PR (feat/NELLUP-42-my-feature)
        │
        ▼ GitHub App webhook fires
        │
        ▼ FastAPI verifies HMAC → enqueues task (Redis)
        │
        ▼ Celery worker:
            1. Fetches PR diff from GitHub
            2. Fetches ticket NELLUP-42 from Plane
            3. Haiku classifies: trivial / moderate / complex
            4. Sonnet reviews (if moderate/complex) with full context
            5. Posts review comment on GitHub PR
            6. Posts summary on Plane task
            7. Moves Plane state → QA/Testing or Refused
```

---

## Phase 1 Setup (local, test repo)

### 1. Create a GitHub App

Go to GitHub → Settings → Developer Settings → GitHub Apps → New GitHub App.

Fill in:
- **Name:** `Review App (test)`
- **Homepage URL:** `http://localhost:8000`
- **Webhook URL:** `https://your-ngrok-url.ngrok.io/webhooks/github`
- **Webhook secret:** any random string (save it for `.env`)

Permissions:
- Repository → Contents: **Read**
- Repository → Pull requests: **Read & Write**
- Repository → Metadata: **Read**

Subscribe to events: `Pull request`

After creating, generate a **Private Key** (downloads a `.pem` file) and note the **App ID**.

Install the App on your test repo.

### 2. Clone and configure

```bash
git clone https://github.com/zetainc-co/review-app
cd review-app

cp .env.example .env
# Edit .env with your values:
#   GITHUB_APP_ID=12345
#   GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
#   GITHUB_WEBHOOK_SECRET=your-webhook-secret
#   ANTHROPIC_API_KEY=sk-ant-...
#   PLANE_API_KEY=plane_api_...
```

### 3. Configure Plane states

Edit `review-config.yml`:

```yaml
plane:
  workspace_slug: "your-workspace"
  project_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  states:
    code_review: "UUID"     # get from Plane API
    qa_testing: "UUID"
    refused: "UUID"
```

Get state UUIDs:
```bash
curl -H "x-api-key: YOUR_KEY" \
  "https://api.plane.so/api/v1/workspaces/YOUR_SLUG/projects/YOUR_PROJECT_ID/states/"
```

### 4. Run

```bash
# Start everything
docker-compose up

# Or locally without Docker:
pip install -e ".[dev]"
redis-server &
celery -A app.worker worker --loglevel=info &
uvicorn app.main:app --reload
```

### 5. Expose webhook (ngrok)

```bash
ngrok http 8000
# Copy the https URL → paste in GitHub App webhook settings
```

### 6. Test

Create a branch named `feat/PROJ-42-test-review` in your test repo, open a PR, and watch:
- GitHub PR gets a review comment
- Plane task PROJ-42 gets a comment
- Plane task moves state automatically

---

## Branch naming convention

The app extracts the Plane issue ID from the branch name using this pattern:

```
feat/NELLUP-42-my-feature     ✅  extracts 42
fix/PROJ-7-fix-bug             ✅  extracts 7
NELLUP-100/feature-name        ✅  extracts 100
my-random-branch               ⚠️  no Plane update (no ID found)
```

---

## Configuration reference (`review-config.yml`)

| Key | Default | Description |
|---|---|---|
| `thresholds.trivial_max_lines` | 50 | PRs under this line count → auto-approved, Haiku only |
| `thresholds.max_diff_lines` | 3000 | PRs over this → skip (too large) |
| `models.classify` | `claude-haiku-4-5-20251001` | Classification model |
| `models.review` | `claude-sonnet-4-6` | Review model |
| `plane.workspace_slug` | required | Your Plane workspace slug |
| `plane.project_id` | required | UUID of the Plane project |
| `plane.states.*` | required | UUIDs of target states |
| `review.inject_claude_md` | true | Inject `CLAUDE.md` into review prompt |
| `review.auto_transition_state` | true | Move Plane state automatically |

---

## Project context (CLAUDE.md)

Add a `CLAUDE.md` to any connected repo root. The reviewer will inject it into every review prompt.

Example:
```markdown
# My Project — Review Context

## Critical rules
- Never store passwords in plaintext
- All API endpoints require authentication
- Database queries must use parameterized statements

## Architecture
- Backend: FastAPI + PostgreSQL
- Auth: JWT with 1h expiry
```

---

## Cost

| Volume | Monthly Claude cost |
|---|---|
| 100 PRs/month | ~$2.40 |
| 500 PRs/month | ~$12 |
| 1000 PRs/month | ~$24 |

Haiku classifies every PR (~$0.003/PR). Sonnet only reviews complex PRs (~$0.07/PR, ~30% of volume).

---

## Roadmap

- **Phase 2:** Tree-sitter repo indexer — automatic CLAUDE.md generation, semantic context per PR
- **Phase 3:** Next.js dashboard — connected repos, dev metrics, review history

---

## License

MIT
