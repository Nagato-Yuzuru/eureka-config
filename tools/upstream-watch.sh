#!/usr/bin/env bash
# Upstream watch, run by .github/workflows/upstream-watch.yml: diff $PATHSPEC in the clone at $1
# from the reviewed point to its tip and open, or update, one issue labeled $LABEL with a summary,
# the commits, and the diff. The reviewed point is the head marker of the newest closed $LABEL
# issue; an open one keeps its own base, so it accumulates until closed.
#
# The tip is HEAD, or with TRACK=releases the newest v* tag: upstreams whose files ship with a
# release are reviewed at what was released, not at unreleased work on the branch, and the issue
# carries the notes of the releases in range.
#
# env: UPSTREAM (owner/repo, for links), PATHSPEC (whitespace-separated git pathspecs; `:!x`
# excludes), LABEL, optional TRACK (head | releases), and gh's GH_REPO / GH_TOKEN.
set -euo pipefail

clone=$1
read -ra specs <<<"$PATHSPEC"

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

case "${TRACK:-head}" in
  head) tip=$(git -C "$clone" rev-parse HEAD) ;;
  releases)
    # sed reads to the end, so git never meets a closed pipe (head would, and pipefail fails it).
    tag=$(git -C "$clone" tag --list 'v*' --sort=-v:refname | sed -n 1p)
    if [[ -z "$tag" ]]; then
      echo "::error::TRACK=releases but $UPSTREAM has no v* tag"
      exit 1
    fi
    tip=$(git -C "$clone" rev-parse "$tag^{commit}")
    ;;
  *)
    echo "::error::TRACK is head or releases, not $TRACK"
    exit 1
    ;;
esac

diff=$(git -C "$clone" diff "$base" "$tip" -- "${specs[@]}")
if [[ -z "$diff" ]]; then
  echo "nothing under $PATHSPEC changed since ${base:0:12}"
  exit 0
fi

# The size of the change per included pathspec, each with every exclusion applied.
summary() {
  local excludes=() spec stat
  for spec in "${specs[@]}"; do
    if [[ "$spec" == :!* || "$spec" == ':(exclude)'* ]]; then excludes+=("$spec"); fi
  done
  for spec in "${specs[@]}"; do
    if [[ "$spec" == :!* || "$spec" == ':(exclude)'* ]]; then continue; fi
    # The +-form: bash before 4.4 calls an empty array unbound under set -u.
    stat=$(git -C "$clone" diff --shortstat "$base" "$tip" -- "$spec" ${excludes[@]+"${excludes[@]}"})
    if [[ -n "$stat" ]]; then echo "- \`$spec\`:$stat"; fi
  done
}

body=$(mktemp)
{
  echo "<!-- upstream-watch base=$base head=$tip -->"
  echo "\`$UPSTREAM\` \`$PATHSPEC\`, ${base:0:12}..${tip:0:12}${tag:+ ($tag)}:" \
    "https://github.com/$UPSTREAM/compare/$base...$tip"
  echo
  echo "Port what earns it into our fork, then close this issue to mark ${tip:0:12} reviewed."
  echo
  echo "## Summary"
  echo
  summary
  if [[ -n "${tag:-}" ]]; then
    # Tags already reachable from the reviewed point were reviewed with it.
    git -C "$clone" tag --list 'v*' --merged "$tip" --no-merged "$base" --sort=-v:refname |
      while read -r t; do
        notes=$(gh release view "$t" -R "$UPSTREAM" --json body --jq .body)
        echo
        echo "### $t"
        echo
        head -c 8000 <<<"$notes"
      done
  fi
  echo
  echo "## Commits"
  echo
  git -C "$clone" log --format='- %h %ad %s' --date=short "$base..$tip" -- "${specs[@]}"
  echo
} >"$body"
# GitHub caps an issue body at 65536 characters; past that, the compare link has the rest.
budget=$((60000 - $(wc -c <"$body")))
if ((budget > 0)); then
  {
    echo "<details><summary>diff</summary>"
    echo
    echo '`````diff'
    head -c "$budget" <<<"$diff"
    echo
    echo '`````'
    echo "</details>"
  } >>"$body"
fi

if [[ -n "$open" ]]; then
  gh issue edit "$open" --body-file "$body"
else
  gh label create "$LABEL" --color ededed --force
  gh issue create --title "upstream-watch: $UPSTREAM $PATHSPEC" --label "$LABEL" --body-file "$body"
fi
