#!/usr/bin/env python3
"""Generate standalone landing pages (/landing/<slug>.html) for every ready
product in revenue-os.db.

MIRRORS the storefront's real buy-CTA ladder exactly (see storefront
app/main.py `_render_cta` / product page):
  - product has a valid `stripe_sku`  -> POST /audit/checkout session form
    (webhook fulfills + receipts).  A sku we can actually serve is one whose
    OFFERS entry exists in the audit-api catalog; if we can't confirm it, do
    NOT emit a form that will 400.
  - product with no usable sku but a real buy.stripe.com checkout_url ->
    direct anchor to that Payment Link (the storefront's /p/{slug} behaviour).
  - contact checkout -> /contact?product=... link.
  - otherwise (no checkout) -> contact CTA so the lead is never lost (no
    dead end / no broken form).

Previously this wrote the /audit/checkout form for EVERY product, which meant
non-sku products (comfyui-workflow-pack, n8n-automation-kit, ...) posted a sku
the audit-api didn't know and got a 400 Invalid SKU on a served page — a real
broken purchase path that the cross-layer verifier correctly caught.

Run: .venv/bin/python scripts/generate_landing_pages.py
"""
from __future__ import annotations
import html
import json
import sqlite3
from pathlib import Path

DB = "/home/scott/ai-lab/revenue-os/revenue-os.db"
LANDING_DIR = Path("/home/scott/ai-lab/reports/landing")
LANDING_DIR.mkdir(parents=True, exist_ok=True)

# The audit-api OFFERS catalog is the single source of truth for which sku the
# /audit/checkout Session flow actually accepts.
OFFERS_DIR = Path("/home/scott/ai-workspace/repos/ai-lab-audit-api/app/catalog.py")


def _valid_skus() -> set[str]:
    """Return the set of skus the audit-api checkout endpoint accepts."""
    p = OFFERS_DIR
    if not p.is_file():
        print("WARN: catalog.py not found; assuming no valid skus")
        return set()
    try:
        # Find the OFFERS dict literal and parse sku string keys before ': Offer'.
        txt = p.read_text(errors="ignore")
        skus = set()
        for line in txt.splitlines():
            if ":" not in line:
                continue
            key, _, rest = line.partition(":")
            key = key.strip().strip('"').strip("'")
            if key and "Offer" in rest:
                skus.add(key)
        return skus
    except Exception as exc:  # pragma: no cover
        print(f"  WARN: could not parse catalog: {exc}")
        return set()


valid_skus = _valid_skus()


def _cta(sku: str, checkout_url: str, gumroad_url: str = "") -> str:
    """Build the correct buy CTA, mirroring the storefront product page."""
    checkout = (checkout_url or "").strip()
    gumroad = (gumroad_url or "").strip()
    parts = []
    # 1) Sovereign Session flow: ONLY when we can confirm the sku exists in the
    #    audit-api OFFERS catalog. Otherwise a form posts an unknown sku -> 400.
    if sku and sku in valid_skus:
        parts.append(
            f'<form method=post action="/audit/checkout"><input type=hidden '
            f'name=sku value="{html.escape(sku, quote=True)}">'
            f'<button type=submit class=cta>⚡ Get It Now →</button></form>'
        )
    # 2) Direct Stripe Payment Link (real, starts with buy.stripe.com)
    elif checkout.startswith("https://buy.stripe.com/"):
        parts.append(
            f'<a class=cta href="{html.escape(checkout, quote=True)}" '
            f'target=_blank rel=noopener>⚡ Get Pro →</a>'
        )
    # 3) Gumroad fallback commerce authority
    elif gumroad and "gumroad.com" in gumroad:
        parts.append(
            f'<a class=cta href="{html.escape(gumroad, quote=True)}" '
            f'target=_blank rel=noopener>⬆ Also on Gumroad →</a>'
        )
    # 4) Contact-only / pricing-on-contact
    elif "contact" in checkout.lower():
        parts.append(f'<a class=cta href="/contact?product={html.escape(sku, quote=True)}">📩 Contact for pricing →</a>')
    # 5) No usable path: contact so the lead is never lost (no broken form).
    else:
        parts.append(f'<a class=cta href="/contact?product={html.escape(sku, quote=True)}">📩 Get access →</a>')
    return "".join(parts)


def landing_html(row: dict) -> str:
    slug = row["slug"]
    name = row.get("name") or slug
    price = row.get("price") or "$19"
    audience = row.get("audience") or ""
    pain = row.get("pain") or ""
    offer = row.get("offer") or ""
    sku = (row.get("stripe_sku") or "").strip()
    checkout = (row.get("checkout_url") or "").strip()
    gumroad = (row.get("gumroad_url") or "").strip()
    cta = _cta(sku, checkout, gumroad)
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{name} — AI Automated Systems</title>
<style>body{{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:760px;margin:0 auto;padding:2rem 1rem;color:#171717;line-height:1.6}}
h1{{font-size:2rem;margin-bottom:.3rem}} .price{{font-size:1.5rem;font-weight:700;color:#6d28d9;margin:.5rem 0}}
.badge{{display:inline-block;background:#f3e8ff;color:#6d28d9;border-radius:999px;padding:.2rem .8rem;font-size:.8rem;margin:.2rem}}
.cta-row{{margin:1.5rem 0}} .cta{{display:inline-block;background:#6d28d9;color:#fff;border:0;border-radius:.5rem;
padding:.9rem 1.6rem;font-size:1.05rem;cursor:pointer;text-decoration:none}} .cta:hover{{background:#5b21b6}}
form{{display:inline-block}} .cards{{display:flex;gap:.6rem;flex-wrap:wrap;margin:1rem 0}}
.card{{flex:1;min-width:200px;border:1px solid #e5e7eb;border-radius:.6rem;padding:1rem}}
footer{{margin-top:2rem;font-size:.8rem;color:#6b7280}}</style></head>
<body>
<h1>{html.escape(name)}</h1>
<span class=price>{html.escape(price)}</span>
<div>{''.join(f'<span class=badge>{html.escape(b)}</span>' for b in [audience, pain, offer] if b)}</div>
<p>{html.escape(offer)}</p>
<div class=cta-row>{cta}</div>
<div class=cards>
<div class=card><b>Sovereign</b><br>100% local-first delivery. No vendor lock-in.</div>
<div class=card><b>Instant</b><br>Checkout redirects to Stripe; receipt emailed on payment.</div>
<div class=card><b>Low-risk</b><br>$9–$19. Built to be useful the moment you open it.</div>
</div>
<footer>AI Automated Systems · aiautomatedsystems.ca · local-first sovereign commerce</footer>
</body></html>"""


def main() -> int:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM products WHERE status='ready'").fetchall()
    n = 0
    for r in rows:
        p = LANDING_DIR / f"{r['slug']}.html"
        p.write_text(landing_html(dict(r)))
        n += 1
    print(f"WROTE {n} landing pages to {LANDING_DIR} (valid_skus={len(valid_skus)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())