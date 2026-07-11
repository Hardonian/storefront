# AI Automated Systems Storefront

Public-facing product catalog, lead capture, and local-first conversion analytics
for the AI lab revenue OS. Serves the catalog of products from the revenue-os
state, renders per-product buyer pages, captures newsletter leads, and tracks
page views + checkout clicks into a local SQLite `events` table — no third-party
analytics dependency.

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
  flags.py       # live feature-flag engine (A/B, trust bar, newsletter)
  downloads.py   # signed instant-download URLs
  templates/     # server-rendered catalog + legal pages
pyproject.toml   # deps + build config
run.sh           # launch script
justfile         # dev/test tasks
scripts/         # bootstrap + ops helpers
BOOTSTRAP.md     # universal bootstrap kit (one-command setup)
```

## What it actually serves (verified)

- `GET  /`                         — catalog grid of all products (image, price,
                                     audience, ready/draft badge, buy CTA). Honors
                                     feature flags (hero/cta variant, trust bar,
                                     newsletter, grid density).
- `GET  /p/{slug}`                 — per-product buyer page (offer, price, trust
                                     pills, preview link, Buy Now / Gumroad CTA).
                                     Records a `product_view` event.
- `GET  /api/products`             — JSON catalog (full columns).
- `POST /api/leads`                — lead capture (rate-limited, email-validated).
- `POST /api/subscribe`            — newsletter capture (rate-limited).
- `POST /api/track`                — local-first analytics ingestion
                                     (page_view / product_view / checkout_click).
- `GET  /api/analytics`            — conversion stats (operator-only, `X-API-Key`).
- `GET  /api/leads`                — lead list (operator-only, `X-API-Key`).
- `GET  /api/gpu-status`           — live GPU free-% from compute-api (best-effort).
- `GET  /legal/{doc}`              — Terms / Privacy / Refund rendered from the
                                     real markdown in `ai-lab/legal`.
- `GET  /robots.txt` `GET /sitemap.xml` — SEO surfaces.

## Security

- Operator endpoints (`/api/leads`, `/api/analytics`) require `X-API-Key`.
- Security headers (CSP, X-Frame-Options, Referrer-Policy, Permissions-Policy) on
  every response.
- CORS restricted to `GET`.
- Leads are rate-limited (20 req/min/IP) and email-validated.
- Legal routes are allow-listed to known documents; markdown is HTML-escaped
  (no raw-HTML or path traversal).

## Run

```bash
cp .env.example .env          # set API_KEY for operator endpoints
./run.sh                      # uvicorn on :8020
```

Durable service:

```bash
systemctl --user restart storefront.service
curl http://127.0.0.1:8020/health
curl -s http://127.0.0.1:8020/api/products | head
```

## Notes
- Product definitions are read live from the revenue-os SQLite state.
- Public-facing checkout links are NOT embedded in this README; the live buy
  URLs live on the product pages (sourced from revenue-os).
- The buyer catalog at `/` and `/p/{slug}` is the real conversion surface — it is
  server-rendered from the same data the sitemap and JSON API expose.

## 🛒 Store
Self-hosted AI infra, bundles & compute access: [aiautomatedsystems.ca](https://aiautomatedsystems.ca)
