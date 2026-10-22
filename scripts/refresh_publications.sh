#!/usr/bin/env bash
# Refresh the publication list end to end.
#
# Split by network reachability, not preference:
#   - crisapi (Pure API, has DOIs) answers ONLY on the university network (n3).
#     The Mac and the coder pod cannot reach it at all.
#   - Everything else (Pure Portal RSS, ORCID, Crossref) is public.
#
# So the API mirror runs over ssh on n3 and its index is copied back. Set
# PURE_API_HOST=none to skip that step and rely on the cached index.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
PURE_API_HOST="${PURE_API_HOST:-n3}"
REMOTE_DIR="${REMOTE_DIR:-~/healthai-website}"
DELAY="${DELAY:-12}"     # Cloudflare fronts the portal; do not lower this casually

echo "== 1/5 Pure Portal RSS (per person, $DELAY s apart) =="
$PY -u scripts/fetch_pure_publications.py --delay "$DELAY"

if [ "$PURE_API_HOST" != "none" ]; then
  echo "== 2/5 Pure API mirror on $PURE_API_HOST =="
  ssh -o BatchMode=yes "$PURE_API_HOST" \
    "cd $REMOTE_DIR && $PY -u scripts/fetch_pure_api.py --size 400"
  scp -q "$PURE_API_HOST:$REMOTE_DIR/cache/pure_api_titles.json" cache/
else
  echo "== 2/5 skipped (PURE_API_HOST=none), using cached index =="
fi

echo "== 3/5 resolve DOIs (exact Pure join, then Crossref fallback) =="
$PY -u scripts/join_pure_dois.py
$PY -u scripts/resolve_pure_dois.py

echo "== 4/5 ORCID + Crossref =="
$PY -u scripts/fetch_orcid_works.py
$PY -u scripts/fetch_crossref.py

echo "== 5/5 build =="
$PY -u scripts/build_publications.py

if ! git diff --quiet content/publications.generated.yaml; then
  echo "publications.generated.yaml changed:"
  git diff --stat content/publications.generated.yaml
else
  echo "no change"
fi
