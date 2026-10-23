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

# WORK INSIDE A FRESH CLONE when we have somewhere to push to.
#
# The container image deliberately carries no .git -- .dockerignore excludes
# it -- so `git diff` in the image's /work printed its usage text and the job
# failed after a completely successful build ("publications: 636 -> 639",
# then exit 1). There was no repository to diff against, let alone push from.
#
# Cloning also fixes a subtler bug. The shrink guard compares against the
# previous count, and reading that from the image means comparing against
# whatever was baked in at build time. Weeks later that baseline is stale, and
# a run could shrink the live file while comfortably passing the check. The
# only baseline worth having is current main.
#
# Scripts still come from the image, not the clone, so what runs is the pinned
# sha and stays legible from the services manifest.
WORKDIR="$PWD"
if [ "$PUSH_MODE" != "none" ]; then
  REPO="${REPO:-git@github.com:MaastrichtU-IDS/healthai-website.git}"
  CLONE="${CLONE:-/tmp/healthai-website}"
  rm -rf "$CLONE"
  echo "== 0/4 clone $REPO =="
  # Not --depth 1: pushing from a shallow clone can be refused with
  # "shallow update not allowed", and this repo is small.
  git clone --quiet "$REPO" "$CLONE"
  # Keep the caches on the PVC; only content and git metadata come from the clone.
  rm -rf "$CLONE/cache"
  ln -s "$WORKDIR/cache" "$CLONE/cache"
  # rm first: cp -r into an existing directory nests it ($CLONE/scripts/scripts).
  rm -rf "$CLONE/scripts"
  cp -r "$WORKDIR/scripts" "$CLONE/scripts"
  cd "$CLONE"
fi

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

# Without a clone there is no repository to diff against, and `git diff` then
# prints its --no-index usage and exits non-zero rather than saying so.
if [ ! -d .git ]; then
  echo "not a git checkout; nothing to commit (PUSH_MODE=$PUSH_MODE)"
  exit 0
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
      # `gh` is NOT in the container image (bash, git, openssh-client,
      # netcat-openbsd, ca-certificates). `gh pr create || true` therefore
      # swallowed a 127 and the job exited 0 having pushed a branch and opened
      # nothing. If this mode is ever used in the cluster, the image needs gh
      # and a token; say so rather than pretending to have opened a PR.
      command -v gh >/dev/null || { echo "PUSH_MODE=pr needs gh, which is not installed." >&2; exit 1; }
      b="publications-refresh-$(date +%Y%m%d)"
      # force-with-lease: a same-day rerun would otherwise be rejected as a
      # non-fast-forward. The branch is this job's alone, so replacing it is right.
      git push -q --force-with-lease origin "HEAD:$b"
      gh pr create --head "$b" --title "Refresh publications ($before -> $after)" \
         --body "Automated refresh. $before -> $after publications."
    fi ;;
esac
