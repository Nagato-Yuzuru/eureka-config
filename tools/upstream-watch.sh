#!/usr/bin/env bash
# Upstream watch, run by .github/workflows/upstream-watch.yml: diff $PATHSPEC in the clone at $1
# from the reviewed point to its HEAD and open, or update, one issue labeled $LABEL with the
# commits and the diff. The reviewed point is the head marker of the newest closed $LABEL issue;
# an open one keeps its own base, so it accumulates until closed.
#
# env: UPSTREAM (owner/repo, for links), PATHSPEC, LABEL, and gh's GH_REPO / GH_TOKEN.
set -euo pipefail

clone=$1

# The sha after `<key>=` on the marker line of the body on stdin, or nothing.
marker() {
  { grep -m1 '<!-- upstream-watch ' || true; } | { grep -oE " $1=[0-9a-f]{40}" || true; } | cut -d= -f2
}

open=$(gh issue list --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')
if [[ -n "$open" ]]; then
  base=$(gh issue view "$open" --json body --jq .body | marker base)
else
  base=$(gh issue list --label "$LABEL" --state closed --limit 1 --json body --jq '.[0].body // empty' | marker head)
fi
if [[ -z "$base" ]]; then
  echo "::error::no reviewed point: close an issue labeled $LABEL carrying <!-- upstream-watch head=<sha> -->"
  exit 1
fi

tip=$(git -C "$clone" rev-parse HEAD)
diff=$(git -C "$clone" diff "$base" "$tip" -- "$PATHSPEC")
if [[ -z "$diff" ]]; then
  echo "nothing under $PATHSPEC changed since ${base:0:12}"
  exit 0
fi

body=$(mktemp)
{
  echo "<!-- upstream-watch base=$base head=$tip -->"
  echo "\`$UPSTREAM\` \`$PATHSPEC\`, ${base:0:12}..${tip:0:12}:" \
    "https://github.com/$UPSTREAM/compare/$base...$tip"
  echo
  echo "Port what earns it into our fork, then close this issue to mark ${tip:0:12} reviewed."
  echo
  git -C "$clone" log --format='- %h %ad %s' --date=short "$base..$tip" -- "$PATHSPEC"
  echo
  echo "<details><summary>diff</summary>"
  echo
  echo '`````diff'
  # GitHub caps an issue body at 65536 characters; past that, the compare link has the rest.
  head -c 55000 <<<"$diff"
  echo
  echo '`````'
  echo "</details>"
} >"$body"

if [[ -n "$open" ]]; then
  gh issue edit "$open" --body-file "$body"
else
  gh label create "$LABEL" --color ededed --force
  gh issue create --title "upstream-watch: $UPSTREAM $PATHSPEC" --label "$LABEL" --body-file "$body"
fi
