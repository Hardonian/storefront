#!/usr/bin/env python3
"""Generate standalone landing pages (/landing/<slug>.html) for every ready
product in revenue-os.db. Each page has the sovereign buy form (POST to
/audit/checkout) so it converts without depending on the dynamic product page.
Run: .venv/bin/python scripts/generate_landing_pages.py
"""
from __future__ import annotations
import json, sqlite3
from pathlib import Path

DB = "/home/scott/ai-lab/revenue-os/revenue-os.db"
LANDING_DIR = Path("/home/scott/ai-lab/reports/landing")
LANDING_DIR.mkdir(parents=True, exist_ok=True)


def landing_html(row: dict) -> str:
    slug = row["slug"]
    name = row.get("name") or slug
    price = row.get("price") or "$19"
    audience = row.get("audience") or ""
    pain = row.get("pain") or ""
    offer = row.get("offer") or ""
    sku = row.get("stripe_sku") or slug
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
<h1>{name}</h1>
<span class=price>{price}</span>
<div>{''.join(f'<span class=badge>{b}</span>' for b in [audience, pain, offer] if b)}</div>
<p>{offer}</p>
<div class=cta-row>
<form method=post action="/audit/checkout">
<input type=hidden name=sku value="{sku}">
<button type=submit class=cta>⚡ Get It Now →</button>
</form>
</div>
<div class=cards>
<div class=card><b>Sovereign</b><br>100% local-first delivery. No vendor lock-in.</div>
<div class=card><b>Instant</b><br>Checkout redirects to Stripe; receipt emailed on payment.</div>
<div class=card><b>Low-risk</b><br>$9–$19. Built to be useful the moment you open it.</div>
</div>
<footer>AI Automated Systems · aiautomatedsystems.ca · local-first sovereign commerce</footer>
</body></html>"""


def main() -> int:
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM products WHERE status='ready'").fetchall()
    n = 0
    for r in rows:
        p = LANDING_DIR / f"{r['slug']}.html"
        p.write_text(landing_html(dict(r)))
        n += 1
    print(f"WROTE {n} landing pages to {LANDING_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
