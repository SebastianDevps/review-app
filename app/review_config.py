"""Load and cache review-config.yml."""

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG: dict = {
    "thresholds": {
        "trivial_max_lines": 50,
        "complex_min_lines": 300,
        "max_diff_lines": 3000,
    },
    "models": {
        "classify": "claude-haiku-4-5-20251001",
        "review": "claude-sonnet-4-6",
    },
    "plane": {},
    "review": {
        "inject_claude_md": True,
        "inject_plane_ticket": True,
        "post_github_comment": True,
        "post_plane_comment": True,
        "auto_transition_state": True,
        "blocking_severities": ["critical", "high"],
    },
}


@lru_cache(maxsize=1)
def load_config(path: str = "review-config.yml") -> dict:
    """
    Load review-config.yml with fallback to defaults.
    Cached after first load — restart app to reload config.
    """
    config_path = Path(path)
    if not config_path.exists():
        logger.warning("review-config.yml not found, using defaults")
        return _DEFAULT_CONFIG

    with config_path.open() as f:
        config = yaml.safe_load(f) or {}

    # Deep merge with defaults so missing keys don't cause KeyErrors
    merged = _deep_merge(_DEFAULT_CONFIG, config)
    logger.info("Loaded config from %s", config_path.resolve())
    return merged


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, preferring override values."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
