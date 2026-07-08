# AI Automated Systems Storefront

Public-facing product catalog, lead capture, and local-first conversion analytics
for the AI lab revenue OS. Serves the 23 ready products (real Stripe/Gumroad
checkouts), captures newsletter leads, and tracks page views + checkout clicks
into a local SQLite `events` table — no third-party analytics dependency.

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

## What it does

- `/` — catalog grid of all products (image, price, audience, ready/draft badge, buy CTA)
- `/p/{slug}` — per-product landing page (static HTML from `/home/scott/ai-lab/reports/landing/`)
- `/api/products` · `/api/products/{slug}` — JSON catalog
- `/api/lead` · `/api/subscribe` — lead + newsletter capture (rate-limited, 20 req/min/IP)
- `/api/track` — local-first analytics ingestion (page_view / checkout_click)
- `/api/analytics` — conversion stats (operator-only, requires `X-API-Key`)
- `/api/leads` — lead list (operator-only, requires `X-API-Key`)
- `/legal/{doc}` — Terms / Privacy / Refund rendered from markdown
- `/landing-assets/*` — product images + `analytics.js`

## Security

- All paid/product endpoints are **public catalog reads only**; leads are rate-limited
  and email-validated; operator endpoints (`/api/leads`, `/api/analytics`) require `X-API-Key`.
- Security headers (CSP, X-Frame-Options, Referrer-Policy, Permissions-Policy) set on every response.
- CORS restricted to `GET`.
- Slugs/doc names sanitised (no path traversal).

## Run

```bash
cp .env.example .env          # set API_KEY for operator endpoints
./run.sh                      # uvicorn on :8020 (uses local or command-center venv)
```

Durable service:

```bash
systemctl --user restart storefront.service
curl http://127.0.0.1:8020/health
```

## Notes
- Designed to read product definitions from the revenue-os state and render buyer-facing pages.
- Bootstrap-ready: `BOOTSTRAP.md` provides a universal, idempotent setup path (Python/Node/Go/generic).
- Public-facing checkout links are intentionally NOT embedded in READMEs; see the product subpages / revenue-os operator for live buy URLs.

## Analytics note

`analytics.js` POSTs to the storefront's own `/api/track` (same origin). The old
remote endpoint `aiautomatedsystems.ca/api/track` is unreachable; do not re-point
there. Checkout links carry `data-slug` so clicks map to the right product.
