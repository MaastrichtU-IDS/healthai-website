#!/usr/bin/env bash
# Refresh the LinkedIn feed on content/linkedin.generated.yaml and push it.
#
# Modelled on refresh_publications.sh and run by the same cluster namespace
# (MaastrichtU-IDS/services, ids3/projects/healthai-website/dev), on its own
# clock. Kept separate from the publication job on purpose: a LinkedIn outage
# or an expired token should not stop the publication list from refreshing,
# and the two have nothing in common but a git push.
#
# NETWORK. The pod has no direct route out; HTTPS_PROXY in refresh-config
# sends api.linkedin.com and the image CDN through the egress-proxy, which
# allows any destination from a cluster source. Nothing extra is needed.
#
# TOKEN. The pod gets LINKEDIN_REFRESH_TOKEN plus the client id and secret,
# and the fetcher exchanges them for an access token every run. An access
# token lasts 60 days, so storing one would have the job stop two months
# later with nothing on the outside to say so.
#
# WHY IT COMMITS IMAGES TOO. LinkedIn's CDN URLs expire within hours, so the
# page cannot link to them; the pictures have to live in the repo. They are
# downscaled to 900px before committing. At ~90KB each and two or three new
# posts a month, this adds a few MB a year.
#
# NOTIFICATION. A failed CronJob tells nobody, so this script says so itself
# through scripts/notify.py: a GitHub issue, which emails whoever watches the
# repo, and the in-cluster Slack relay. Both are optional, both are
# best-effort, and neither can change this script's exit status. The warning
# about the refresh token's own expiry goes the same way, thirty days ahead,
# because that one is a date nothing in here can move.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
PUSH_MODE="${PUSH_MODE:-none}"       # none | push
MIN_RATIO="${MIN_RATIO:-0.80}"       # refuse to publish a sharp shrink
SCAN="${LINKEDIN_SCAN:-150}"
KEEP="${LINKEDIN_KEEP:-35}"
OUT=content/linkedin.generated.yaml
IMGDIR=assets/linkedin

LOG=$(mktemp)
DAYS_FILE=$(mktemp)
export LINKEDIN_TOKEN_DAYS_FILE="$DAYS_FILE"
# Keep the whole run's output, so a notification can carry the tail of it
# rather than "exit 1". tee, not a plain redirect: the pod log still gets
# everything as it happens. fds 3 and 4 hold the real stdout and stderr so the
# exit handler can put them back and let tee drain; reading $LOG while tee is
# still writing to it loses exactly the lines that explain the failure.
exec 3>&1 4>&2
exec > >(tee -a "$LOG") 2>&1

notify() {  # notify <level> <key> <title> <body>
  "$PY" "$WORKDIR_SCRIPTS/notify.py" --level "$1" --key "$2" --title "$3" --body "$4" || true
}

on_exit() {
  rc=$?
  exec 1>&3 2>&4   # close the pipe to tee
  wait             # and let it finish writing $LOG
  if [ "$rc" -ne 0 ]; then
    notify fail linkedin-refresh-failed \
      "LinkedIn feed refresh failed (exit $rc)" \
      "$(tail -40 "$LOG")"
  fi
  rm -f "$LOG" "$DAYS_FILE"
  exit "$rc"
}
trap on_exit EXIT

# notify.py comes from the image, not the clone: the clone is cded into below
# and the trap has to keep working from there.
WORKDIR_SCRIPTS="$PWD/scripts"

# Work inside a fresh clone, for the same two reasons as the publication job:
# the image carries no .git, and the shrink guard has to compare against
# current main rather than whatever was baked in at build time. Mirrored
# images come from the clone too, which is what stops every run re-downloading
# all 35 of them and burning the daily /rest/images quota.
WORKDIR="$PWD"
if [ "$PUSH_MODE" != "none" ]; then
  REPO="${REPO:-git@github.com:MaastrichtU-IDS/healthai-website.git}"
  CLONE="${CLONE:-/tmp/healthai-website-linkedin}"
  rm -rf "$CLONE"
  echo "== 0/2 clone $REPO =="
  git clone --quiet "$REPO" "$CLONE"
  rm -rf "$CLONE/scripts"
  cp -r "$WORKDIR/scripts" "$CLONE/scripts"
  cd "$CLONE"
fi

count() { $PY -c "import yaml;d=yaml.safe_load(open('$OUT'));print(len(d.get('social',[])))" 2>/dev/null || echo 0; }
before=$(count)

echo "== 1/2 fetch =="
$PY -u scripts/fetch_linkedin_posts.py --scan "$SCAN" --keep "$KEEP"

after=$(count)
echo "linkedin posts: $before -> $after"

# The refresh token keeps its original year-long expiry; LinkedIn does not
# rotate it. Thirty days is enough notice to re-run linkedin_auth.py and
# update Vault, and notify.py will not open a second issue while the first is
# still open, so this says it once rather than every week.
days=$(cat "$DAYS_FILE" 2>/dev/null || echo "")
if [ -n "$days" ] && [ "$days" -lt 30 ] 2>/dev/null; then
  notify warn linkedin-token-expiring \
    "LinkedIn refresh token expires in $days days" \
    "The weekly LinkedIn feed refresh stops when it lapses, and the news page
will serve whatever it last pushed.

To renew: run scripts/linkedin_auth.py on a machine with the client
credentials, then put the new refresh_token into Vault at
secret/healthai-website/dev as LINKEDIN_REFRESH_TOKEN.

Close this issue once Vault has the new value."
fi

# A token that has gone stale, or a filter that stops matching because
# people.yaml was restructured, both end in a near-empty file that looks like
# a successful run. Refuse to publish that.
if [ "$after" -eq 0 ]; then
  echo "ABORT: no posts survived the filter. Not publishing." >&2
  exit 1
fi
if [ "$before" -gt 0 ]; then
  ok=$($PY -c "print(1 if $after >= $before * $MIN_RATIO else 0)")
  if [ "$ok" != "1" ]; then
    echo "ABORT: $after is below $MIN_RATIO of $before. Not publishing." >&2
    exit 1
  fi
fi

if [ ! -d .git ]; then
  echo "not a git checkout; nothing to commit (PUSH_MODE=$PUSH_MODE)"
  exit 0
fi

new_images=$(git ls-files --others --exclude-standard -- "$IMGDIR")

# The fetch date is rewritten every run, so a week with no new posts still
# leaves a one-line diff. Committing that would put a weekly empty change on a
# public repo and rebuild the site for nothing. Compare the posts themselves,
# and if only the date moved, put the file back.
if [ -z "$new_images" ] && git diff --quiet -- "$IMGDIR"; then
  if $PY - "$OUT" <<'EOF'; then
import subprocess, sys, yaml
path = sys.argv[1]
head = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True)
if head.returncode:
    sys.exit(1)                       # not in HEAD yet: a real change
old = (yaml.safe_load(head.stdout) or {}).get("social")
new = (yaml.safe_load(open(path)) or {}).get("social")
sys.exit(0 if old == new else 1)
EOF
    git checkout -- "$OUT"
    echo "no change (only the fetch date moved)"
    exit 0
  fi
fi

if git diff --quiet -- "$OUT" "$IMGDIR" && [ -z "$new_images" ]; then
  echo "no change"; exit 0
fi
git diff --stat -- "$OUT" "$IMGDIR" || true

case "$PUSH_MODE" in
  none) echo "PUSH_MODE=none; leaving the change uncommitted." ;;
  push)
    # Only the feed and its images: nothing else in the tree is this job's
    # business, and a stray edit from the clone must never ride along.
    git add -- "$OUT" "$IMGDIR"
    git -c user.name="linkedin-bot" -c user.email="noreply@maastrichtuniversity.nl" \
        commit -q -m "Refresh LinkedIn feed ($before -> $after posts)"
    git push -q origin HEAD:main && echo "pushed to main" ;;
  *) echo "unknown PUSH_MODE=$PUSH_MODE" >&2; exit 1 ;;
esac
