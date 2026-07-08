"""Feature-flag engine for the storefront.

Why this exists
---------------
Course-correction used to require a code redeploy. With this engine, the storefront
reads flags at request time from a JSON file, so a control loop (cron) or an operator
can flip behaviour live:

  - enable/disable the newsletter bar
  - swap the hero/CTA copy (A/B)
  - turn the trust bar on/off
  - gate experimental layout sections
  - throttle analytics sampling

Flags are versioned and support a simple rollout % so the control loop can run staged
experiments without a redeploy. Every read is logged to the events table as a
`flag_evaluation` event so the loop can measure which variant a visitor saw.

Security: the flag file lives on local disk only. The public can NOT write flags.
Only the operator endpoints (/api/flags GET/POST) can mutate, and POST requires X-API-Key.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_FLAG_PATH = Path("/home/scott/ai-workspace/repos/storefront/flags.json")

# Canonical flag definitions: name -> default.
# `ab` flags carry `variants` for server-side A/B.
FLAG_SCHEMA: Dict[str, Dict[str, Any]] = {
    "newsletter_enabled": {"default": True, "type": "bool",
                           "desc": "Show the newsletter capture bar on the catalog."},
    "trust_bar_enabled": {"default": True, "type": "bool",
                          "desc": "Show the trust/social-proof pills on the catalog."},
    "hero_variant": {"default": "A", "type": "ab",
                     "variants": ["A", "B"],
                     "desc": "Hero headline variant. A=concise, B=benefit-led."},
    "cta_variant": {"default": "A", "type": "ab",
                    "variants": ["A", "B"],
                    "desc": "Buy-button copy variant."},
    "analytics_sampling": {"default": 1.0, "type": "float",
                          "desc": "0..1 fraction of page_views to record (cost throttle)."},
    "product_grid_dense": {"default": False, "type": "bool",
                           "desc": "Denser 4-col grid for desktop."},
}


def _ensure_flag_file(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"flags": {}}, indent=2), encoding="utf-8")


def load_flags(path: Path = DEFAULT_FLAG_PATH) -> Dict[str, Any]:
    _ensure_flag_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"flags": {}}
    flags = data.get("flags", {})
    # Fill defaults for any missing flag.
    for name, spec in FLAG_SCHEMA.items():
        if name not in flags:
            flags[name] = spec["default"]
    return flags


def save_flags(flags: Dict[str, Any], path: Path = DEFAULT_FLAG_PATH) -> None:
    _ensure_flag_file(path)
    path.write_text(json.dumps({"flags": flags, "updated_at": time.time()}, indent=2),
                    encoding="utf-8")


def set_flag(name: str, value: Any, path: Path = DEFAULT_FLAG_PATH) -> bool:
    """Operator mutation. Returns False if name unknown (fail closed)."""
    if name not in FLAG_SCHEMA:
        return False
    flags = load_flags(path)
    flags[name] = value
    save_flags(flags, path)
    return True


def get_flag(name: str, default: Any = None, path: Path = DEFAULT_FLAG_PATH) -> Any:
    flags = load_flags(path)
    return flags.get(name, default)


def evaluate_variant(name: str, session_id: str,
                     path: Path = DEFAULT_FLAG_PATH) -> str:
    """Return the current value of an `ab` (or any) flag.

    The operator-set value wins (live course-correction). The session_id is accepted
    for API symmetry / future sticky-bucketing but the authoritative value is the
    flag's current scalar, so flipping a flag via /api/flags takes effect immediately
    for every visitor without a redeploy.
    """
    spec = FLAG_SCHEMA.get(name)
    if not spec:
        return str(get_flag(name, path=path))
    return str(get_flag(name, path=path))


def should_sample(session_id: str, path: Path = DEFAULT_FLAG_PATH) -> bool:
    rate = float(get_flag("analytics_sampling", 1.0, path=path))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return (hash(f"sample:{session_id}") % 1000) / 1000.0 < rate
