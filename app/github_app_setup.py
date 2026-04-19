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
CREDENTIALS_FILE = "/data/github_app_credentials.json"


# ── Step 1 — Setup landing page ───────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    """Landing page — shows current status and 'Connect GitHub' button."""
    creds = _load_credentials()
    if creds:
        installed = creds.get("installation_id")
        status_html = f"""
        <div class="status ok">
          ✅ GitHub App configurada — App ID: <strong>{creds.get('app_id')}</strong><br>
          {'✅ Instalada en repositorios' if installed else '⚠️ Aún no instalada en ningún repositorio'}
        </div>
        {'<a href="/setup/install" class="btn">Instalar en un repositorio</a>' if not installed else ''}
        <p><a href="/">← Ir al dashboard</a></p>
        """
    else:
        status_html = """
        <div class="status pending">
          ⏳ GitHub App aún no configurada
        </div>
        <p>Haz clic en el botón para crear e instalar la GitHub App automáticamente.</p>
        <a href="/setup/github" class="btn">Conectar con GitHub →</a>
        """

    return HTMLResponse(_setup_html("Setup — Review App", status_html))


# ── Step 2 — Redirect to GitHub with manifest ─────────────────────────────────

@router.get("/github")
def redirect_to_github(request: Request) -> HTMLResponse:
    """
    GitHub App Manifest flow: POST a manifest to GitHub via auto-submit form.
    GitHub creates the app and redirects to /setup/callback?code=...
    """
    base_url = str(request.base_url).rstrip("/")
    state = secrets.token_urlsafe(16)

    manifest = {
        "name": "Review App",
        "url": base_url,
        "hook_attributes": {
            "url": f"{base_url}/webhooks/github",
            "active": True,
        },
        "redirect_url": f"{base_url}/setup/callback",
        "callback_urls": [f"{base_url}/setup/callback"],
        "setup_url": f"{base_url}/setup/install",
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

    # GitHub App Manifest flow requires a form POST — we use an auto-submit form
    html = f"""<!DOCTYPE html>
<html>
<head><title>Conectando con GitHub...</title></head>
<body>
  <p>Redirigiendo a GitHub para crear la App...</p>
  <form id="manifest-form" method="post" action="https://github.com/settings/apps/new?state={state}">
    <input type="hidden" name="manifest" value='{manifest_json}'>
  </form>
  <script>document.getElementById('manifest-form').submit();</script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Step 3 — GitHub callback (exchange code for credentials) ──────────────────

@router.get("/callback")
async def github_callback(code: str, state: str | None = None) -> HTMLResponse:
    """
    GitHub redirects here after the user confirms the App creation.
    Exchange the one-time code for App credentials (app_id, private_key, etc.)
    """
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

    _save_credentials(creds)

    # Write to .env so the app picks it up on next start
    _update_env(creds)

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
    creds = _load_credentials()
    if not creds:
        return JSONResponse({"configured": False})
    return JSONResponse({
        "configured": True,
        "app_id": creds.get("app_id"),
        "app_slug": creds.get("app_slug"),
        "has_private_key": bool(creds.get("private_key")),
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
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
  </style>
</head>
<body>
  <h1>🤖 Review App — Setup</h1>
  {body}
</body>
</html>"""
