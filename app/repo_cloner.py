"""
Repo cloner — download a repo snapshot for indexing.

Strategy:
  - Uses GitHub API to download the repo as a tarball (no git history needed)
  - Extracts to /tmp/repos/{repo_slug}/
  - Much faster than `git clone --depth=1` for large repos
  - Cleans up old snapshots before downloading fresh ones

Why tarball over git clone:
  - No SSH/HTTPS credential management
  - No .git directory (smaller disk footprint)
  - GitHub API tarball = HEAD of default branch, always fresh
  - ~5x faster for large repos (no object packing overhead)
"""

import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

from app.github_auth import get_installation_token

logger = logging.getLogger(__name__)

REPOS_BASE_DIR = Path("/tmp/repos")
GITHUB_API = "https://api.github.com"


def get_repo_dir(repo_full_name: str) -> Path:
    """Return the local directory path for a repo snapshot."""
    safe_name = repo_full_name.replace("/", "_")
    return REPOS_BASE_DIR / safe_name


def clone_repo_for_indexing(
    installation_id: int,
    repo_full_name: str,
    ref: str = "HEAD",
) -> Path:
    """
    Download a repo snapshot from GitHub and extract it locally.

    Args:
        installation_id: GitHub App installation ID for auth.
        repo_full_name: e.g. 'zetainc-co/nellup'
        ref: branch, tag, or commit SHA (default: HEAD = default branch)

    Returns:
        Path to the extracted repo directory.

    Raises:
        RuntimeError: If download or extraction fails.
    """
    token = get_installation_token(installation_id)
    repo_dir = get_repo_dir(repo_full_name)

    # Clean up existing snapshot
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    REPOS_BASE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading snapshot for %s@%s", repo_full_name, ref)

    # GitHub tarball endpoint
    url = f"{GITHUB_API}/repos/{repo_full_name}/tarball/{ref}"

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        # Stream download to temp file
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            with client.stream(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Failed to download repo tarball: {response.status_code}"
                    )
                with tmp_path.open("wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)

        logger.info("Downloaded %s (%.1f MB)", repo_full_name, tmp_path.stat().st_size / 1_048_576)

        # Extract tarball — GitHub wraps content in a top-level directory
        extract_dir = REPOS_BASE_DIR / "_extract_tmp"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)

        with tarfile.open(tmp_path, "r:gz") as tar:
            # Security: strip any absolute paths or path traversal
            safe_members = [
                m for m in tar.getmembers()
                if not m.name.startswith("/") and ".." not in m.name
            ]
            tar.extractall(path=extract_dir, members=safe_members)

        # GitHub wraps in a directory like 'zetainc-co-nellup-abc1234/'
        # Move the inner directory to the final location
        inner_dirs = list(extract_dir.iterdir())
        if not inner_dirs:
            raise RuntimeError("Tarball was empty after extraction")

        shutil.move(str(inner_dirs[0]), str(repo_dir))
        shutil.rmtree(extract_dir)

        logger.info("Extracted %s → %s", repo_full_name, repo_dir)
        return repo_dir

    except Exception as exc:
        # Clean up partial extraction
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        raise RuntimeError(f"Failed to clone repo {repo_full_name}: {exc}") from exc

    finally:
        tmp_path.unlink(missing_ok=True)


def cleanup_repo(repo_full_name: str) -> None:
    """Remove the local repo snapshot to free disk space."""
    repo_dir = get_repo_dir(repo_full_name)
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
        logger.info("Cleaned up local snapshot for %s", repo_full_name)


def repo_is_cached(repo_full_name: str) -> bool:
    """Check if a local snapshot exists for a repo."""
    return get_repo_dir(repo_full_name).exists()
