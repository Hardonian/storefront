# Sovereign Storefront & Platform Evidence Console

[![Test Suite](https://img.shields.io/badge/pytest-61%20passed%20%7C%20100%25-success)](tests/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](pyproject.toml)
[![Architecture](https://img.shields.io/badge/architecture-Domain--Driven%20Modular-teal)](app/)
[![License](https://img.shields.io/badge/license-Commercial%20%2F%20Proprietary-lightgrey)](LICENSE)

**Storefront** is the critical public revenue, product catalog, and conversion telemetry gateway in the **Hardonia / AI Automated Systems** sovereign software stack.

It presents an ultra-fast, local-first catalog of air-gapped AI tools, autonomous workflow suites, deterministic governance packages, and private GPU compute instances — backed by local SQLite persistence, zero cloud analytics leaks, HMAC-signed asset delivery, and strict browser security defenses.

---

## 🏛️ Architectural Overview

Storefront is built as a domain-driven, modular FastAPI application decoupled into clean layers:

```
storefront/
├── app/
│   ├── core/                  # Core foundations & runtime configuration
│   │   ├── config.py          # Typed Pydantic Settings & brand resolution
│   │   ├── database.py        # SQLite connection pool with WAL mode & busy timeout
│   │   ├── security.py        # HMAC-SHA256 tokens, URL defense, slug sanitization
│   │   └── templates.py       # Jinja2 environment & custom filters
│   │
│   ├── middleware/            # High-performance ASGI middleware stack
│   │   ├── security_headers.py# CSP, X-Frame-Options: DENY, Referrer & Permission policies
│   │   ├── cors_and_limits.py # Strict CORS origin allowlist & 64KB payload bounds
│   │   ├── rate_limiter.py    # Per-IP token bucket rate limiting with honeypot traps
│   │   ├── cache_control.py   # Immutable asset caching & no-store payment controls
│   │   └── request_context.py # Session resolution, request tracking, bot classification
│   │
│   ├── routers/               # Domain-driven API & web controllers
│   │   ├── catalog.py         # Home grid, /p/{slug}, /pricing, /free-audit-guide
│   │   ├── api_products.py    # Public sanitized JSON product catalog
│   │   ├── api_leads.py       # Lead capture, newsletter subscribe, queued privacy erasure
│   │   ├── commerce.py        # Checkout routing, /buyer portal, /order/success, /download/{slug}
│   │   ├── api_analytics.py   # Privacy funnel truth ingestion, conversion metrics
│   │   ├── api_flags.py       # Live feature flags, A/B experiments, sampling controls
│   │   ├── api_support.py     # AI support assistant proxy & /support-widget.js
│   │   ├── status.py          # /health, /status.json, /proof-score, Prometheus metrics
│   │   ├── legal.py           # Markdown legal reader with directory traversal protection
│   │   ├── blog.py            # Local AI ops field notes & RSS feed
│   │   ├── seo.py             # robots.txt, sitemap.xml, llms.txt, IndexNow, Google site verification
│   │   └── private_ai_ops.py  # Private AI operations landing & synthetic demo suite
│   │
│   ├── services/              # Encapsulated business domain services
│   │   ├── product_service.py # Catalog querying, sorting, enrichment
│   │   ├── analytics_service.py # SQLite events & aggregation
│   │   └── ai_assistant_service.py # Upstream assistant proxy with leak protection
│   │
│   ├── main.py                # Application entrypoint & middleware assembly
│   ├── store.py               # Database CRUD abstraction
│   ├── flags.py               # Feature flag engine with active experiment control
│   └── downloads.py           # HMAC download token generator
│
├── static/                    # Fast static CSS, JS, SVGs, and landing assets
├── tests/                     # 100% passing test suite (61 contracts)
│   ├── test_storefront.py
│   ├── test_flags.py
│   ├── test_funnel_truth.py
│   ├── test_private_ai_operations_public.py
│   ├── test_observability.py
│   └── test_security_contract.py
│
├── scripts/                   # Operational & developer tooling
│   ├── healthcheck.py         # CLI deployment health validator
│   └── seed_sample_catalog.py # Sample catalog seeder
│
├── systemd/                   # Native Linux systemd service unit
│   └── storefront.service
├── Dockerfile                 # Multi-stage hardened production container
├── docker-compose.yml         # Containerized orchestration
├── run.ps1                    # PowerShell launcher (Windows)
└── run.sh                     # Bash launcher (Linux/macOS)
```

---

## 🛡️ Security & Privacy Model

Storefront is engineered to strict sovereign data-privacy standards:

1. **Zero External Telemetry Leaks**:
   - Page views, clicks, and conversions are recorded directly to local SQLite (`events` and `funnel_events` tables).
   - No Google Analytics, no Facebook Pixels, no third-party JavaScript tracking tags.
2. **Defensive HTTP Headers**:
   - `Content-Security-Policy`: Disallows unsafe inline scripts on payment and fulfillment surfaces; restricts framing to `frame-ancestors 'none'`.
   - `X-Frame-Options: DENY`: Complete clickjacking defense.
   - `Referrer-Policy: no-referrer`: Prevents leaking internal paths or session metadata.
   - `Permissions-Policy`: Restricts browser geolocation, microphone, and camera access.
3. **SSRF & Checkout Authority Protection**:
   - External checkout URLs are strictly checked via `safe_external_url()`.
   - Rejects non-standard ports, userinfo `@` authority tricks, and unallowlisted hostnames.
4. **Honeypot Bot Traps & Rate Limiting**:
   - Form inputs include hidden honeypot fields (`website`) that silently flag bot traffic into `CLASS_LIKELY_BOT` without disrupting genuine users.
   - Per-IP rate limiting (20 requests/minute) protects against brute force attacks and lead spam.
5. **HMAC-SHA256 Signed Deliverables**:
   - Digital bundles are downloaded through tamper-proof expiring tokens (`/download/{slug}?expires=...&token=...`) with strict directory traversal prevention.
6. **Queued Privacy Erasure**:
   - GDPR/CCPA erasure requests via `/api/privacy/erase` are queued into `privacy_requests` with `status='pending_verification'` rather than executing unverified destructive deletion.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.11+ or 3.12+
- `uv` (recommended) or `pip`

### 1. Local Development Launch

**Using PowerShell (Windows):**
```powershell
.\run.ps1
```

**Using Bash (Linux / macOS):**
```bash
chmod +x run.sh
./run.sh
```

The application will start on **`http://127.0.0.1:8020`**.

### 2. Seeding Sample Products
To seed the catalog with realistic sample products for testing:
```bash
python scripts/seed_sample_catalog.py
```

### 3. Running Verification & Tests
Storefront comes with 61 rigorous automated test contracts:
```bash
uv run --with pytest --with httpx pytest
```

---

## 🐳 Docker Deployment

### Run with Docker Compose
```bash
docker compose up -d --build
```

### Build & Run Container Standalone
```bash
docker build -t hardonia/storefront:latest .
docker run -d -p 8020:8020 --name storefront hardonia/storefront:latest
```

---

## ⚙️ Configuration & Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `STOREFRONT_DB_PATH` | `~/ai-lab/revenue-os/revenue-os.db` | Primary SQLite database path |
| `ANALYTICS_DB_PATH` | `""` (falls back to primary DB) | Analytics & funnel SQLite database |
| `API_KEY` | `""` | Operator authentication key (`X-API-Key` header) |
| `DOWNLOAD_SECRET` | `storefront_download_hmac_secret` | HMAC key for signing bundle URLs |
| `STOREFRONT_FLAGS_PATH` | `./flags.json` | Feature flags and experiment state |
| `BUNDLES_DIR` | `~/ai-lab/bundles` | Directory containing deliverable `.zip` files |
| `LEGAL_DIR` | `~/ai-lab/legal` | Directory containing `.md` terms, privacy, refund policies |
| `CONTENT_DRAFTS_DIR` | `~/ai-lab/reports/content/drafts` | Directory containing blog post `.md` files |
| `SUPPORT_BOT_URL` | `http://127.0.0.1:8070` | Upstream AI support assistant endpoint |

---

## 📊 Core Endpoints Summary

### Public Buyer Surfaces
- `GET /` — Public catalog grid with live Proof Score, GPU capacity widget, and product cards
- `GET /p/{slug}` — Product buyer page with JSON-LD schema, specifications, and checkout CTA
- `GET /pricing` — Transparent fixed-price portfolio grid
- `GET /buy/{slug}` — Safe checkout redirector with channel attribution tracking
- `GET /buyer` — Delivery and order recovery portal
- `GET /order/success` — Secure purchase confirmation & entitlement claim surface
- `GET /free-audit-guide` — Interactive sovereign readiness guide
- `GET /tools/gpu-cost-calculator` — Self-hosted vs cloud GPU cost calculator
- `GET /status` — Visual system status & GPU farm telemetry dashboard
- `GET /proof-score` — Certified zero-telemetry and execution index
- `GET /blog` — Practical engineering field notes and deployment guides

### REST & Telemetry APIs
- `GET /api/products` — Sanitized JSON catalog (internal paths stripped)
- `POST /api/leads` — Lead capture with rate-limiting and validation
- `POST /api/subscribe` — Newsletter subscription with honeypot bot trap
- `POST /api/track` — Local-first privacy funnel analytics ingestion
- `POST /api/fulfillment/claim` — Upstream fulfillment engine claim proxy
- `POST /api/privacy/erase` — Queued privacy erasure requests
- `POST /api/ask` — AI support assistant inquiry (64KB payload limit)
- `GET /api/flags` — Feature flag schema & values (`X-API-Key` required)
- `POST /api/flags/experiment` — Start/stop A/B experiments (`X-API-Key` required)
- `GET /metrics/funnel` — Privacy funnel truth metrics (`X-API-Key` required)
- `GET /health` — Microsecond liveness probe
- `GET /status.json` — Cached system health snapshot

---

## 🛠️ Operational Verification

Verify live deployment using the included CLI healthcheck tool:
```bash
python scripts/healthcheck.py http://127.0.0.1:8020
```

Sample output:
```text
Checking Storefront at http://127.0.0.1:8020...

[OK] Liveness Probe (http://127.0.0.1:8020/health) -> HTTP 200
     Status: ok
[OK] System Status Snapshot (http://127.0.0.1:8020/status.json) -> HTTP 200
     Status: operational
[OK] Public Catalog API (http://127.0.0.1:8020/api/products) -> HTTP 200

All Storefront health gates passed! (Status: GREEN)
```

---

## 📜 License & Sovereign Stack Heritage
Part of the **Hardonia Sovereign AI Operating Stack**. Built for independent operators who demand verifiable execution, complete local ownership, and zero data leakage.
