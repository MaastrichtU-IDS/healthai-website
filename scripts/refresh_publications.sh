#!/usr/bin/env bash
# Refresh the publication list end to end.
#
# One source for attribution (Pure/CRIS API, per person, DOIs included) plus
# Crossref for metadata. There is no portal scraping, no title matching and no
# second machine: an earlier version had all three, before the API's ID_type
# parameter was found (services#597).
#
# NETWORK: crisapi answers only on the university network. Set PURE_HTTPS_PROXY
# to the egress-proxy when running in a cluster pod; leave it unset on a node,
# which reaches 137.120.0.0/16 directly. Do NOT rely on ambient HTTPS_PROXY:
# n3's points at the general university proxy, which returns 503 for crisapi.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
PUSH_MODE="${PUSH_MODE:-none}"       # none | pr | push
MIN_RATIO="${MIN_RATIO:-0.90}"       # refuse to publish a sharp shrink
OUT=content/publications.generated.yaml

before=$($PY -c "import yaml;d=yaml.safe_load(open('$OUT'));print(len(d['publications'] if isinstance(d,dict) else d))" 2>/dev/null || echo 0)

echo "== 1/4 Pure/CRIS, per person =="
$PY -u scripts/fetch_pure_publications.py

echo "== 2/4 ORCID =="
$PY -u scripts/fetch_orcid_works.py

echo "== 3/4 Crossref metadata (ORCID + Pure DOIs) =="
$PY -u scripts/fetch_crossref.py

echo "== 4/4 build =="
$PY -u scripts/build_publications.py

after=$($PY -c "import yaml;d=yaml.safe_load(open('$OUT'));print(len(d['publications'] if isinstance(d,dict) else d))")
echo "publications: $before -> $after"

# A partial fetch that shrinks a public list is the worst failure here, and it
# would look like success. Refuse to publish it; a human can rerun.
if [ "$before" -gt 0 ]; then
  ok=$($PY -c "print(1 if $after >= $before * $MIN_RATIO else 0)")
  if [ "$ok" != "1" ]; then
    echo "ABORT: $after is below $MIN_RATIO of $before. Not publishing." >&2
    exit 1
  fi
fi

if git diff --quiet "$OUT"; then echo "no change"; exit 0; fi
git diff --stat "$OUT"

case "$PUSH_MODE" in
  none) echo "PUSH_MODE=none; leaving the change uncommitted." ;;
  pr|push)
    # Only ever this one file: nothing else in the tree is this job's business.
    git add "$OUT"
    git -c user.name="publications-bot" -c user.email="noreply@maastrichtuniversity.nl" \
        commit -q -m "Refresh publications ($before -> $after)"
    if [ "$PUSH_MODE" = push ]; then
      git push -q origin HEAD:main && echo "pushed to main"
    else
      b="publications-refresh-$(date +%Y%m%d)"
      git push -q origin "HEAD:$b"
      gh pr create --head "$b" --title "Refresh publications ($before -> $after)" \
         --body "Automated refresh. $before -> $after publications." || true
    fi ;;
esac
