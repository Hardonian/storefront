# Sovereign Storefront & Platform Evidence Console

[![Test Suite](https://img.shields.io/badge/pytest-105%20passed%20%7C%20100%25-success)](tests/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](pyproject.toml)
[![Architecture](https://img.shields.io/badge/architecture-Domain--Driven%20Modular-teal)](app/)
[![Autonomous Learning](https://img.shields.io/badge/learning-Bayesian%20Thompson%20Bandit-orange)](app/services/bandit_service.py)
[![License](https://img.shields.io/badge/license-Commercial%20%2F%20Proprietary-lightgrey)](LICENSE)

**Storefront** is the critical public revenue, product catalog, autonomous optimization, and conversion telemetry gateway in the **Hardonia / AI Automated Systems** sovereign software stack.

It presents an ultra-fast, local-first catalog of air-gapped AI tools, autonomous workflow suites, deterministic governance packages, and private GPU compute instances — backed by local SQLite persistence, zero cloud analytics leaks, Bayesian multi-armed bandit learning, offline cryptographic license issuance, and strict browser security defenses.

---

## 🏛️ System Architecture

Storefront is built as a domain-driven, modular FastAPI application decoupled into clean layers:

```
storefront/
├── app/
│   ├── core/                  # Core foundations & runtime configuration
│   │   ├── config.py          # Typed Pydantic Settings & stack connective tissue
│   │   ├── database.py        # SQLite connection pool with WAL mode & 15s timeout
│   │   ├── security.py        # HMAC-SHA256 tokens, URL defense, license signing
│   │   └── templates.py       # Jinja2 environment & custom filters
│   │
│   ├── middleware/            # High-performance ASGI middleware stack
│   │   ├── security_headers.py# CSP, X-Frame-Options: DENY, Referrer & Permission policies
│   │   ├── cors_and_limits.py # Strict CORS origin allowlist & 64KB payload bounds
│   │   ├── rate_limiter.py    # Per-IP token bucket rate limiting with honeypot traps
│   │   ├── cache_control.py   # Immutable asset caching & no-store payment controls
│   │   └── request_context.py # Session resolution, request tracking, bot classification
│   │
│   ├── services/              # Autonomous & business domain services
│   │   ├── bandit_service.py  # Bayesian Beta-Bernoulli Thompson Sampling bandit
│   │   ├── anomaly_detector.py# Real-time funnel anomaly & conversion stall detector
│   │   ├── stack_bridge.py    # revenue-os, Hermes Ops Nerve Center, and Compute API bridge
│   │   ├── demand_intelligence.py # Customer inquiry mining & intent gap signals
│   │   ├── license_service.py # Air-gapped cryptographic software license engine
│   │   ├── product_service.py # Catalog querying, sorting, enrichment
│   │   └── analytics_service.py # SQLite events & aggregation
│   │
│   ├── routers/               # Domain-driven API & web controllers
│   │   ├── catalog.py         # Home grid, /p/{slug}, /pricing, /free-audit-guide
│   │   ├── tools.py           # /tools/redaction-sandbox, /tools/hardware-sizer, /tools/gpu-cost-calculator
│   │   ├── blueprint.py       # Tailored Sovereign Architecture Blueprint generator (/blueprint/{token})
│   │   ├── commerce.py        # Checkout routing, /buyer locker, /order/success, /download/{slug}
│   │   ├── api_products.py    # Public sanitized JSON product catalog
│   │   ├── api_leads.py       # Lead capture, newsletter subscribe, queued privacy erasure
│   │   ├── api_analytics.py   # Privacy funnel truth ingestion, conversion metrics
│   │   ├── api_flags.py       # Live feature flags, A/B experiments, /api/flags/bandit report
│   │   ├── api_support.py     # AI support assistant proxy & /support-widget.js
│   │   ├── status.py          # /health, /status.json, /proof-score, /api/stack/fleet
│   │   ├── legal.py           # Markdown legal reader with directory traversal protection
│   │   ├── blog.py            # Local AI ops field notes & RSS feed
│   │   ├── seo.py             # robots.txt, sitemap.xml, llms.txt, IndexNow, Google verification
│   │   └── private_ai_ops.py  # Private AI operations landing & synthetic demo suite
│   │
│   └── main.py                # Application entrypoint & middleware assembly
│
├── static/                    # Fast static CSS, JS, SVGs, and landing assets
├── tests/                     # 100% passing test suite (71 contracts)
│   ├── test_bandit_service.py
│   ├── test_anomaly_detector.py
│   ├── test_interactive_tools.py
│   ├── test_buyer_locker.py
│   ├── test_storefront.py
│   ├── test_flags.py
│   ├── test_funnel_truth.py
│   ├── test_private_ai_operations_public.py
│   ├── test_observability.py
│   └── test_security_contract.py
│
├── scripts/                   # Operational & developer tooling
│   ├── healthcheck.py         # CLI deployment health validator
│   ├── simulate_stack_bridge.py # Sovereign stack bridge & fleet telemetry probe
│   ├── run_bandit_evaluation.py # Thompson Sampling Monte Carlo audit
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

## ⚡ Autonomous Capabilities & Sovereign Features

### 1. Bayesian Multi-Armed Bandit (Thompson Sampling)
- Replaces static A/B testing with continuous Bayesian optimization for headlines (`hero_variant`) and CTAs (`cta_variant`).
- Samples from $\text{Beta}(1 + \alpha, 1 + \beta)$ posteriors to balance exploration with exploitation.
- **Autonomous Winner Promotion**: When a variant reaches $\ge 99\%$ win probability with sufficient sample size, Storefront automatically pins the winning copy without manual intervention.

### 2. Real-Time Anomaly & Conversion Stall Detector
- Monitors conversion funnels (`landing` $\to$ `lead_start` $\to$ `checkout_start` $\to$ `provider_payment`).
- Emits structured operational alerts if checkout abandonment spikes or if 5xx errors surge.

### 3. Interactive Zero-Cloud Previews & Sizers
- **Sentinel Redaction Sandbox** (`/tools/redaction-sandbox`): Live regex/WASM sanitization demo for HIPAA/GDPR clinical and legal notes.
- **LLM Hardware Topology Sizer** (`/tools/hardware-sizer`): Calculates exact VRAM requirements, KV cache overhead, GPU topologies, and power budgets for Llama-3, DeepSeek, and Mistral.
- **Dynamic Architecture Blueprint** (`/blueprint/{token}`): Generates personalized enterprise deployment plans on the fly.

### 4. Cryptographic Air-Gapped Licensing & Buyer Locker
- Issues HMAC-SHA256 signed software license certificates (`.lic`) for purchased suites.
- Buyer Locker (`/buyer`) allows verified operators to view entitlement portfolios and download signed update bundles.

---

## 🚀 Quickstart

### 1. Local Launch
**Windows PowerShell:**
```powershell
.\run.ps1
```
**Linux / macOS:**
```bash
./run.sh
```
Server runs on **`http://127.0.0.1:8020`**.

### 2. Verify Fleet Telemetry & Stack Bridge
```bash
python scripts/simulate_stack_bridge.py
```

### 3. Run Automated Tests
```bash
uv run --with pytest --with httpx pytest
```
*Result: 71 / 71 tests passing (100% pass rate).*

---

## 📜 License & Sovereign Stack Heritage
Part of the **Hardonia Sovereign AI Operating Stack**. Built for independent operators who demand verifiable execution, complete local ownership, and zero data leakage.
