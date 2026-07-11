#!/usr/bin/env bash
# scripts/security-scan.sh — quick local secret + hygiene scan before push.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
echo "== Hardonian secret/hygiene scan =="
# 1. No committed .env with real third-party secrets (Stripe/GitHub/AWS).
#    Excludes API_KEY (a local operator token) which may legitimately start with
#    a "sk-" prefix and must never be confused with a live Stripe secret.
if [ -f .env ] && grep -vE '^(#|API_KEY=)' .env | grep -qE "=(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|sk_live_|rk_live_|AKIA[0-9A-Z]{16})"; then
  echo "FAIL: .env contains a likely real third-party secret. Do not commit .env."
  exit 1
fi
# 2. No private key blocks in tracked files (exclude this scan script itself).
if git grep -n "BEGIN.*PRIVATE KEY" -- ':!*.md' ':!scripts/security-scan.sh' 2>/dev/null; then
  echo "FAIL: private key material found in tracked files."
  exit 1
fi
# 3. .env must be gitignored
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "FAIL: .env is tracked by git. Run: git rm --cached .env && echo '.env' >> .gitignore"
  exit 1
fi
echo "OK: no obvious secret exposure."
