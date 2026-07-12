"""conftest — guarantee this repo's `app` package wins over sibling repos.

hardonia-storefront, hardonia-checkout-api and hardonia-compute-api all ship a
top-level `app` package. When pytest detects its rootdir as the shared parent
(/home/scott/ai-workspace/repos), `import app.main` can resolve to the wrong
sibling. This conftest force-inserts THIS repo root at the front of sys.path and
drops any pre-imported `app` modules so the correct one is loaded.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

for _mod in ("app", "app.main", "app.store", "app.flags", "app.downloads", "app.metrics"):
    sys.modules.pop(_mod, None)
