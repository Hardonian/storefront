#!/usr/bin/env python3
"""build_deliverables_manifest.py

Scans /home/scott/hardonia.store/products/<slug>/ and produces a structured
manifest of every product's real assets: cover image, preview doc, walkthrough,
templates, worksheets, scripts, and deliverable files. The storefront reads
this to render media galleries + "what's included" per tier.

Output: /home/scott/ai-lab/reports/deliverables-manifest.json
No external calls. Idempotent.
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path("/home/scott/hardonia.store/products")
OUT = Path("/home/scott/ai-lab/reports/deliverables-manifest.json")

# file extensions we treat as "showcase media / sample"
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".svg"}
DOC_EXT = {".md", ".txt", ".pdf"}
CODE_EXT = {".py", ".sh", ".service", ".timer", ".json", ".yaml", ".yml", ".csv", ".toml"}

SKIP = {"README.md", "CHANGELOG.md", "MANIFEST", "LICENSE", "product.json", "PREVIEW.md", "WALKTHROUGH.md", "SUPPORT.md"}

def rel(p: Path, base: Path) -> str:
    return str(p.relative_to(base))

def scan_product(slug: str, base: Path) -> dict:
    info = {"slug": slug, "cover": None, "preview_md": None, "walkthrough_md": None,
            "templates": [], "worksheets": [], "scripts": [], "samples": [], "media": []}
    if not base.exists():
        return info
    # cover
    for cand in ("assets/cover.png", "assets/cover.jpg", "cover.png"):
        c = base / cand
        if c.exists():
            info["cover"] = f"/product-assets/{slug}/{cand}"
            break
    # preview / walkthrough docs
    for name, key in (("PREVIEW.md", "preview_md"), ("WALKTHROUGH.md", "walkthrough_md"), ("SUPPORT.md", "support_md")):
        p = base / name
        if p.exists():
            info[key] = f"/product-assets/{slug}/{name}"
    # recurse for templates/worksheets/scripts/samples
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        r = rel(f, base)
        if r.split("/")[-1] in SKIP:
            continue
        ext = f.suffix.lower()
        url = f"/product-assets/{slug}/{r}"
        low = r.lower()
        if ext in MEDIA_EXT:
            info["media"].append({"name": r, "url": url})
        elif "/templates/" in low or r.endswith(".service") or r.endswith(".timer"):
            info["templates"].append({"name": r, "url": url})
        elif "worksheet" in low or "template" in low or ext == ".csv":
            info["worksheets"].append({"name": r, "url": url})
        elif ext in CODE_EXT and ("scripts/" in low or ext in (".sh", ".py")):
            info["scripts"].append({"name": r, "url": url})
        elif ext in (".json", ".md", ".txt") and ("deliverables/" in low or "samples/" in low or "sample" in low):
            info["samples"].append({"name": r, "url": url})
    return info

def main():
    manifest = {}
    if ROOT.exists():
        for d in sorted(ROOT.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                manifest[d.name] = scan_product(d.name, d)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2))
    # summary
    n_cover = sum(1 for v in manifest.values() if v["cover"])
    n_prev = sum(1 for v in manifest.values() if v["preview_md"])
    n_tmpl = sum(len(v["templates"]) for v in manifest.values())
    n_samp = sum(len(v["samples"]) for v in manifest.values())
    print(f"products={len(manifest)} covers={n_cover} previews={n_prev} templates={n_tmpl} samples={n_samp}")
    print(f"wrote {OUT}")

if __name__ == "__main__":
    main()
