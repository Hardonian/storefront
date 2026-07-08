# storefront

FastAPI-based product storefront for the Hardonia revenue OS. Serves product pages, offer routing, and fulfillment links for digital products (ComfyUI workflow packs, audit reports, GPU compute access).

## Stack
- Python 3.11+
- FastAPI + Uvicorn
- Jinja2 templates (`app/templates`)
- Pydantic v2 settings (`pydantic-settings`)
- SQLite-backed product store (`app/store.py`)

## Layout
```
app/
  main.py        # FastAPI app, routes
  store.py       # product/offer data access
  templates/     # server-rendered pages
pyproject.toml   # deps + build config
run.sh           # launch script
justfile         # dev/test tasks
scripts/         # bootstrap + ops helpers
BOOTSTRAP.md     # universal bootstrap kit (one-command setup)
```

## Run
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --port 8020
# or
./run.sh
```

## Notes
- Designed to read product definitions from the revenue-os state and render buyer-facing pages.
- Bootstrap-ready: `BOOTSTRAP.md` provides a universal, idempotent setup path (Python/Node/Go/generic).
- Public-facing checkout links are intentionally NOT embedded in READMEs; see the product subpages / revenue-os operator for live buy URLs.
