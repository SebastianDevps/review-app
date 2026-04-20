"""
GitHub App Manifest flow — self-service installation.

Instead of requiring the user to manually create a GitHub App at
github.com/settings/apps, this module implements the GitHub App Manifest
flow: the user clicks "Connect GitHub" in the UI, we redirect to GitHub
with a pre-filled manifest, GitHub creates the App and redirects back
with a code, we exchange the code for credentials and store them.

Reference: https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest

Flow:
  1. GET  /setup           → renders setup page (or redirects to GitHub)
  2. GET  /setup/github    → POST manifest to GitHub (redirect form)
  3. GET  /setup/callback  → GitHub redirects here with ?code=...
                             We exchange code for App credentials
                             We store: app_id, private_key, webhook_secret, client_id, client_secret
  4. GET  /setup/install   → Redirect to GitHub App installation page
  5. GET  /setup/done      → Installation complete, all configured
"""

import json
import logging
import os
import secrets

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

GITHUB_API = "https://github.com"
CREDENTIALS_FILE = "/app/.github_app_credentials.json"


# ── Step 1 — Setup landing page ───────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    """Landing page — shows current status and public URL form."""
    creds = _load_credentials()

    if creds:
        installed = creds.get("installation_id")
        status_html = f"""
        <div class="status ok">
          ✅ GitHub App configurada — App ID: <strong>{creds.get('app_id')}</strong><br>
          {'✅ Instalada en repositorios' if installed else '⚠️ Aún no instalada en ningún repositorio'}
        </div>
        {'<a href="/setup/install" class="btn">Instalar en un repositorio</a>' if not installed else ''}
        <p><a href="http://localhost:4000/dashboard">← Ir al dashboard</a></p>
        """
        return HTMLResponse(_setup_html("Setup — Review App", status_html))

    # Try to auto-detect ngrok URL
    ngrok_url = await _detect_ngrok_url()

    ngrok_hint = ""
    if ngrok_url:
        ngrok_hint = f'<p class="hint">✅ ngrok detectado: <code>{ngrok_url}</code></p>'

    status_html = f"""
        <div class="status pending">⏳ GitHub App aún no configurada</div>
        <p>Para crear la GitHub App necesitamos una URL pública (ngrok).<br>
           Ejecuta <code>ngrok http 4001</code> en una terminal y pega la URL aquí.</p>
        {ngrok_hint}
        <form method="get" action="/setup/github">
          <label>URL pública (https://xxxx.ngrok-free.app)</label><br>
          <input type="url" name="public_url" required placeholder="https://xxxx.ngrok-free.app"
                 value="{ngrok_url or ''}"
                 style="width:100%;padding:10px;margin:8px 0 16px;border:1px solid #444;
                        background:#1a1a26;color:#e2e2f0;border-radius:6px;font-size:1rem;">
          <button type="submit" class="btn">Crear GitHub App →</button>
        </form>
    """
    return HTMLResponse(_setup_html("Setup — Review App", status_html))


# ── Step 2 — Redirect to GitHub with manifest ─────────────────────────────────

@router.get("/github")
def redirect_to_github(request: Request, public_url: str = "") -> HTMLResponse:
    """
    GitHub App Manifest flow: POST a manifest to GitHub via auto-submit form.
    Uses the provided public_url (ngrok) for webhook — GitHub requires a reachable URL.
    """
    public_url = public_url.rstrip("/")
    if not public_url:
        return HTMLResponse(_setup_html("Error", """
            <div class='status error'>❌ Falta la URL pública.<br>
            Ejecuta <code>ngrok http 4001</code> y vuelve a intentarlo.</div>
            <p><a href='/setup'>← Volver</a></p>
        """))

    # callback comes back to localhost (browser-accessible)
    callback_url = f"http://localhost:4001/setup/callback"
    state = secrets.token_urlsafe(16)

    manifest = {
        "name": "ReviewApp-SebastianDevps",
        "url": public_url,
        "hook_attributes": {
            "url": f"{public_url}/webhooks/github",
            "active": True,
        },
        "redirect_url": callback_url,
        "callback_urls": [callback_url],
        "setup_url": callback_url,
        "description": "AI Code Review — GitHub App + Plane integration",
        "public": False,
        "default_permissions": {
            "contents": "read",
            "pull_requests": "write",
            "metadata": "read",
        },
        "default_events": ["pull_request", "push"],
    }

    manifest_json = json.dumps(manifest)

    html = f"""<!DOCTYPE html>
<html>
<head><title>Conectando con GitHub...</title></head>
<body style="font-family:sans-serif;background:#0a0a0f;color:#e2e2f0;padding:40px;">
  <p>Redirigiendo a GitHub para crear la App...</p>
  <form id="f" method="post" action="https://github.com/settings/apps/new?state={state}">
    <input type="hidden" name="manifest" value='{manifest_json}'>
  </form>
  <script>document.getElementById('f').submit();</script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Step 3 — GitHub callback (exchange code OR save installation_id) ─────────

@router.get("/callback")
async def github_callback(
    code: str | None = None,
    state: str | None = None,
    installation_id: int | None = None,
    setup_action: str | None = None,
) -> HTMLResponse:
    """
    Handles two cases:
    1. Manifest creation callback → code present → exchange for credentials
    2. Post-installation callback → installation_id present → save it
    """
    # ── Case 2: post-installation hook (setup_url) ────────────────────────────
    if installation_id and not code:
        creds = _load_credentials() or {}
        creds["installation_id"] = installation_id
        _save_credentials(creds)
        _update_env(creds)
        logger.info("Saved installation_id=%s for App %s", installation_id, creds.get("app_id"))

        # Save installation_id to DB
        try:
            from app.persistence import save_github_installation_id
            save_github_installation_id(installation_id)
        except Exception as exc:
            logger.warning("Could not save installation_id to DB: %s", exc)

        # Auto-sync repos into DB
        repos_seeded = _sync_repos_to_db(installation_id)
        repos_html = "".join(f"<li><code>{r}</code></li>" for r in repos_seeded)

        return HTMLResponse(_setup_html("Instalación completa ✅", f"""
            <div class='status ok'>
              ✅ GitHub App instalada correctamente.<br>
              Installation ID: <strong>{installation_id}</strong>
            </div>
            <p><strong>{len(repos_seeded)} repositorio(s) conectado(s):</strong></p>
            <ul style="color:#6ee7b7;margin:8px 0 16px">{repos_html or '<li>ninguno aún</li>'}</ul>
            <p>Abre un Pull Request en cualquiera de esos repos y el sistema lo revisará automáticamente.</p>
            <a href="http://localhost:4000/dashboard" class="btn">Ir al Dashboard →</a>
        """))

    if not code:
        return HTMLResponse(_setup_html("Error", """
            <div class='status error'>❌ Callback inválido — falta code e installation_id.</div>
            <p><a href='/setup'>← Volver</a></p>
        """))

    # ── Case 1: manifest conversion ───────────────────────────────────────────
    """Exchange the one-time code for App credentials (app_id, private_key, etc.)"""
    url = f"https://api.github.com/app-manifests/{code}/conversions"

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    if response.status_code != 201:
        logger.error("App manifest conversion failed: %s — %s", response.status_code, response.text)
        return HTMLResponse(_setup_html(
            "Error",
            f"<div class='status error'>❌ Error al crear la App: {response.status_code}<br>{response.text}</div>"
            "<p><a href='/setup'>← Volver</a></p>",
        ))

    data = response.json()

    creds = {
        "app_id": str(data["id"]),
        "client_id": data.get("client_id", ""),
        "client_secret": data.get("client_secret", ""),
        "webhook_secret": data.get("webhook_secret", secrets.token_hex(20)),
        "private_key": data.get("pem", ""),
        "app_slug": data.get("slug", ""),
        "html_url": data.get("html_url", ""),
        "installation_id": None,
    }

    _save_credentials(creds)      # file-based backup
    _update_env(creds)            # .env backup for container restarts

    # ── Save to DB (primary — dynamic, no restart needed) ─────────────────────
    try:
        from app.persistence import save_github_app_config
        save_github_app_config(
            app_id=creds["app_id"],
            app_slug=creds["app_slug"],
            private_key=creds["private_key"],
            webhook_secret=creds["webhook_secret"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
        logger.info("GitHub App credentials saved to DB: app_id=%s", creds["app_id"])
    except Exception as exc:
        logger.warning("Could not save to DB yet (will work after restart): %s", exc)

    logger.info("GitHub App created: id=%s slug=%s", creds["app_id"], creds["app_slug"])

    install_url = f"https://github.com/apps/{creds['app_slug']}/installations/new"

    return HTMLResponse(_setup_html("App Creada ✅", f"""
        <div class='status ok'>
          ✅ GitHub App <strong>{creds['app_slug']}</strong> creada exitosamente.<br>
          App ID: <strong>{creds['app_id']}</strong>
        </div>
        <p>Ahora instala la App en el repositorio que quieres revisar:</p>
        <a href="{install_url}" class="btn" target="_blank">Instalar en repositorio →</a>
        <p style="margin-top:20px; color:#888">
          Las credenciales fueron guardadas automáticamente.<br>
          Reinicia el servidor para aplicarlas: <code>docker-compose restart api worker-reviews worker-indexing</code>
        </p>
    """))


# ── Step 4 — Show credentials (for debugging / manual .env fill) ──────────────

@router.get("/status")
def setup_status() -> JSONResponse:
    """Return current setup status (non-sensitive)."""
    from app.config import settings
    creds = _load_credentials() or {}
    app_id = creds.get("app_id") or settings.github_app_id
    has_key = bool(creds.get("private_key")) or (
        bool(settings.github_app_private_key) and "FILL_ME" not in settings.github_app_private_key
    )
    configured = bool(app_id) and has_key and "FILL_ME" not in str(app_id)
    return JSONResponse({
        "configured": configured,
        "app_id": app_id,
        "app_slug": creds.get("app_slug", "reviewapp-sebastiandevps"),
        "has_private_key": has_key,
        "installation_id": creds.get("installation_id"),
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_credentials() -> dict | None:
    try:
        if os.path.exists(CREDENTIALS_FILE):
            with open(CREDENTIALS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_credentials(creds: dict) -> None:
    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)


def _update_env(creds: dict) -> None:
    """
    Update .env file with new GitHub App credentials.
    This allows docker-compose restart to pick them up automatically.
    """
    env_path = ".env"
    try:
        if os.path.exists(env_path):
            content = open(env_path).read()
        else:
            content = ""

        private_key_escaped = creds["private_key"].replace("\n", "\\n")

        replacements = {
            "GITHUB_APP_ID": creds["app_id"],
            "GITHUB_APP_PRIVATE_KEY": f'"{private_key_escaped}"',
            "GITHUB_WEBHOOK_SECRET": creds["webhook_secret"],
        }

        lines = content.splitlines()
        updated = []
        keys_seen = set()

        for line in lines:
            replaced = False
            for key, value in replacements.items():
                if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                    updated.append(f"{key}={value}")
                    keys_seen.add(key)
                    replaced = True
                    break
            if not replaced:
                updated.append(line)

        # Add any keys not already in .env
        for key, value in replacements.items():
            if key not in keys_seen:
                updated.append(f"{key}={value}")

        with open(env_path, "w") as f:
            f.write("\n".join(updated) + "\n")

        logger.info("Updated .env with GitHub App credentials")
    except Exception as exc:
        logger.warning("Could not update .env: %s", exc)


def _sync_repos_to_db(installation_id: int) -> list[str]:
    """Query GitHub for all repos in this installation and seed the DB."""
    try:
        from app.github_auth import get_installation_token
        from app.persistence import upsert_repository
        import httpx as _httpx

        token = get_installation_token(installation_id)
        with _httpx.Client(timeout=15) as client:
            resp = client.get(
                "https://api.github.com/installation/repositories",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if resp.status_code != 200:
            logger.warning("Could not list repos: %s", resp.status_code)
            return []

        seeded = []
        for r in resp.json().get("repositories", []):
            upsert_repository(
                installation_id=installation_id,
                full_name=r["full_name"],
                default_branch=r.get("default_branch", "main"),
            )
            seeded.append(r["full_name"])
            logger.info("Auto-seeded repo: %s", r["full_name"])
        return seeded
    except Exception as exc:
        logger.warning("_sync_repos_to_db failed: %s", exc)
        return []


async def _detect_ngrok_url() -> str:
    """Try to auto-detect a running ngrok tunnel via the local ngrok API."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get("http://host.docker.internal:4040/api/tunnels")
            if resp.status_code == 200:
                tunnels = resp.json().get("tunnels", [])
                for t in tunnels:
                    if t.get("proto") == "https":
                        return t["public_url"]
    except Exception:
        pass
    return ""


def _setup_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           max-width: 600px; margin: 80px auto; padding: 0 24px; color: #1a1a1a; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 8px; }}
    .status {{ padding: 16px; border-radius: 8px; margin: 20px 0; }}
    .status.ok {{ background: #d1fae5; border: 1px solid #6ee7b7; }}
    .status.pending {{ background: #fef3c7; border: 1px solid #fcd34d; }}
    .status.error {{ background: #fee2e2; border: 1px solid #fca5a5; }}
    .btn {{ display: inline-block; background: #1a1a1a; color: white; padding: 12px 24px;
            border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 8px; }}
    .btn:hover {{ background: #333; }}
    code {{ background: #1a1a26; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; color: #a5b4fc; }}
    .hint {{ color: #6ee7b7; font-size: 0.9em; margin: 4px 0 12px; }}
    label {{ font-size: 0.9em; color: #8888a8; }}
  </style>
</head>
<body>
  <h1>🤖 Review App — Setup</h1>
  {body}
</body>
</html>"""
