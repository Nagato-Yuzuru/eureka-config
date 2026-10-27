#!/usr/bin/env bash
# Upstream watch, run by .github/workflows/upstream-watch.yml: diff $PATHSPEC in the clone at $1
# from the reviewed point to its tip and open, or update, one issue labeled $LABEL with the changed
# files, the commits, and the diff. Upstream's #N references are qualified so they link there. The reviewed point is the head marker of the newest closed $LABEL
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

url="https://github.com/$UPSTREAM"

# The pathspecs as a sentence: the included ones, then the excluded ones after "except".
watched() {
  local in=() out=() spec
  for spec in "${specs[@]}"; do
    case "$spec" in
      ':!'*) out+=("\`${spec#:!}\`") ;;
      ':(exclude)'*) out+=("\`${spec#:(exclude)}\`") ;;
      *) in+=("\`$spec\`") ;;
    esac
  done
  # The +-form: bash before 4.4 calls an empty array unbound under set -u.
  echo "Watching ${in[*]}${out[*]+, except ${out[*]}}"
}

# Each changed file with its line counts.
files() {
  local added deleted path
  git -C "$clone" diff --numstat "$base" "$tip" -- "${specs[@]}" |
    while IFS=$'\t' read -r added deleted path; do
      if [[ "$added" == - ]]; then echo "- \`$path\` (binary)"; else echo "- \`$path\` +$added −$deleted"; fi
    done
}

# A bare #N on stdin is an issue in the upstream, not ours: qualify it so GitHub links there.
upstream_refs() {
  sed -E "s,(^|[^[:alnum:]/&#])#([0-9]+),\\1$UPSTREAM#\\2,g"
}

# Release notes on stdin, their headings pushed two levels down to sit under the release's ###.
demote() {
  awk '/^(```|~~~)/ { fence = !fence }
    !fence && /^#+ / { match($0, /^#+/); n = RLENGTH + 2; if (n > 6) n = 6
      $0 = substr("######", 1, n) substr($0, RLENGTH + 1) }
    { print }'
}

body=$(mktemp)
{
  echo "<!-- upstream-watch base=$base head=$tip -->"
  echo "**[$UPSTREAM]($url)** [\`${base:0:12}...${tip:0:12}\`]($url/compare/$base...$tip)${tag:+ ($tag)}"
  echo
  watched
  echo
  echo "Port what earns it into our fork, then close this issue to mark \`${tip:0:12}\` reviewed."
  echo
  echo "## Files"
  echo
  files
  if [[ -n "${tag:-}" ]]; then
    echo
    echo "## Releases"
    # Tags already reachable from the reviewed point were reviewed with it.
    git -C "$clone" tag --list 'v*' --merged "$tip" --no-merged "$base" --sort=-v:refname |
      while read -r t; do
        notes=$(gh release view "$t" -R "$UPSTREAM" --json body --jq .body)
        echo
        echo "### [$t]($url/releases/tag/$t)"
        echo
        head -c 8000 <<<"$notes" | demote | upstream_refs
      done
  fi
  echo
  echo "## Commits"
  echo
  git -C "$clone" log --format="- [\`%h\`]($url/commit/%H) %ad %s" --date=short "$base..$tip" -- "${specs[@]}" |
    upstream_refs
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

# The range and paths are in the body; the title names the upstream, and the release under review.
title="upstream-watch: $UPSTREAM${tag:+ $tag}"
if [[ -n "$open" ]]; then
  gh issue edit "$open" --title "$title" --body-file "$body"
else
  gh label create "$LABEL" --color ededed --force
  gh issue create --title "$title" --label "$LABEL" --body-file "$body"
fi
