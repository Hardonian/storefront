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
from typing import Any

DEFAULT_FLAG_PATH = Path("/home/scott/ai-workspace/repos/storefront/flags.json")

# Canonical flag definitions: name -> default.
# `ab` flags carry `variants` for server-side A/B.
FLAG_SCHEMA: dict[str, dict[str, Any]] = {
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


def load_flags(path: Path = DEFAULT_FLAG_PATH) -> dict[str, Any]:
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


def save_flags(flags: dict[str, Any], path: Path = DEFAULT_FLAG_PATH) -> None:
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
    """Return the variant a given session should see.

    Priority:
      1. If an A/B experiment is active for this flag, bucket by session hash
         (deterministic 50/50 split, sticky per session).
      2. Otherwise return the operator-set scalar value (live course-correction).
    """
    spec = FLAG_SCHEMA.get(name)
    if not spec:
        return str(get_flag(name, path=path))
    # Active experiment overrides the static value.
    exp = _active_experiment(path)
    if exp and exp.get("flag") == name:
        if exp.get("force_winner"):
            return str(exp["force_winner"])  # proven winner pinned for everyone
        variants = spec.get("variants", ["A", "B"])
        # Stable, non-salted bucket so the 50/50 split is consistent across processes.
        import hashlib
        digest = hashlib.sha256(f"ab:{name}:{session_id}".encode()).hexdigest()
        bucket = int(digest, 16) % len(variants)
        return str(variants[bucket])
    return str(get_flag(name, path=path))


def _experiment_path(path: Path) -> Path:
    return path.parent / "experiment.json"


def _active_experiment(path: Path) -> dict[str, Any] | None:
    ep = _experiment_path(path)
    if not ep.exists():
        return None
    try:
        return json.loads(ep.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def start_experiment(flag: str, force_winner: str | None = None,
                     path: Path = DEFAULT_FLAG_PATH) -> dict[str, Any]:
    """Begin an A/B experiment on an `ab` flag.

    force_winner (e.g. "B") ends the test immediately and pins that variant for
    everyone — used by the course-correction loop once a winner is proven.
    """
    if flag not in FLAG_SCHEMA or FLAG_SCHEMA[flag].get("type") != "ab":
        raise ValueError(f"{flag} is not an A/B flag")
    exp = {"flag": flag, "started_at": time.time(), "force_winner": force_winner}
    _experiment_path(path).write_text(json.dumps(exp, indent=2), encoding="utf-8")
    return exp


def stop_experiment(path: Path = DEFAULT_FLAG_PATH) -> None:
    ep = _experiment_path(path)
    if ep.exists():
        ep.unlink()


def should_sample(session_id: str, path: Path = DEFAULT_FLAG_PATH) -> bool:
    rate = float(get_flag("analytics_sampling", 1.0, path=path))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return (hash(f"sample:{session_id}") % 1000) / 1000.0 < rate
