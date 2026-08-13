# OSS Archaeology

Ordered digs for "why is it designed this way" in an open-source codebase. The evidence is public: git history, the forge (PRs / issues / discussions), in-repo long-form docs, and the wider web. **Hand digs to the user as assignments** — give the exact command or search, let them run it and interpret; you verify the interpretation against `epistemics.md`.

Run the digs in this order; each one either answers the question, yields the pointer into the next layer, or returns a null result (which is itself a finding — tier: unknown).

## 1. Local git history

The last-touch commits and the shape of the change over time.

```bash
git log --oneline -20 -- <file>          # recent commits; PR numbers appear as (#1234)
git blame -L <start>,<end> <file>        # which commit last touched these exact lines
git log --follow -p -- <file>            # full history through renames, with patches
git log -S'<exact literal>' --oneline    # when did this constant/string appear? (pickaxe)
git log -1 --format=%B <commit>          # full message: trailers, issue links, reverts
```

Shallow clone? `git fetch --unshallow` (or `--deepen=200`) before trusting history digs.

## 2. The pull request

Implementation-time rationale captured during review: the problem statement, the debated alternatives, the reviewer pushback.

```bash
gh pr view <n> --repo <owner/repo> --json title,body,author,createdAt,comments,reviews,closingIssuesReferences
```

In a clone of a fork, `--repo` must point at the upstream. Review threads are where rejected designs go to be recorded.

## 3. Issues and discussions

The forcing function — the bug report, the feature request, the user complaint.

```bash
gh issue view <n> --repo <owner/repo> --comments
gh search issues "<keywords>" --repo <owner/repo>   # add --match title for precision
gh search prs "<keywords>" --repo <owner/repo>
```

GitHub Discussions and the issue's linked duplicates often hold more deliberation than the issue itself.

## 4. In-repo long-form

Where projects write the why down before it becomes code: `CHANGELOG.md` (entries often link PRs), `docs/`, `rfcs/`, `design/`, ADR directories, `CONTRIBUTING.md`. Big ecosystems keep formal proposal repos outside the code repo: rust-lang/rfcs, golang/proposal, python PEPs, TC39 proposals, Kubernetes KEPs. If the project has one, search it by feature name.

## 5. The wider web

Maintainer blog posts, conference talks, mailing-list archives (lore.kernel.org, discuss.python.org, project Discourse/Zulip), and HN/lobsters threads where the author shows up in the comments. Web-search the project name + feature name + "design" / "why"; search the author's handle too.

## Reading the results

- **Null results are findings.** "No PR discussion, no issue, no doc" tells you the decision was made silently — likely the author's default, convention, or copy-paste. Say that, at the right tier.
- **Trace back, don't stop at the last commit.** The current shape is usually an accretion; the interesting decision is often several layers down (`--follow`, pickaxe).
- **Numbers came from somewhere.** For thresholds and constants, pickaxe the exact literal and search issues for it before accepting "arbitrary".
- Interpretation discipline — confidence tiers, phrasing, the sycophancy trap — is in `epistemics.md`. Apply it to every claim the dig produces.
