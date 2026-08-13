# Epistemics

How to reason about confidence when evidence is historical, fragmentary, and sometimes contradictory — and how to communicate it without flattening it into false certainty.

Code doesn't carry its own motivation. You can read what code does; you can't read *why it exists*. That lives in commits, PRs, issues, docs, and conversations — all incomplete, biased, and sometimes missing entirely. Pretending otherwise produces confident-sounding guesses that mislead.

Both the user's dig interpretations and your own verifications must follow this framework.

## Confidence Tiers

Every claim about intent or history sits in one of these tiers. The tier determines the phrasing.

### 1. Direct

An explicit, textual citation that answers the question. Not "the code does X so the author must have wanted X" — something an author actually *wrote* that says why.

Examples: a PR description stating the bug it fixes; a code comment saying "clamp to 100 because the upstream API rejects larger values"; an RFC's "alternatives considered" section; a maintainer's mailing-list post explaining the switch.

Phrasing: confident, present tense. "This exists because X." Cite the source.

### 2. Supported

Multiple pieces of indirect evidence converge. No single source states it explicitly, but the pattern across sources makes it likely — e.g. the PR title says "improve performance", the linked issue is a perf report, and the added tests all exercise large inputs.

Phrasing: confident but clearly derived. "The evidence points strongly to X: [the specific pieces]." Cite each piece.

### 3. Inferred

A reasonable reading of the context, but nothing explicitly supports it. The reader should understand this is *interpretation*, not a fact from the record.

Phrasing: hedged — "appears", "likely", "suggests", "is consistent with". Make the chain explicit: "Given A and B, C seems likely because D."

### 4. Speculative

A plausible hypothesis, but the evidence is thin and other explanations fit equally well. Presenting these is valuable — marked clearly as guesses, ideally alongside the competing hypotheses.

Phrasing: "One possibility is X, but there is no direct evidence."

### 5. Unknown

You looked and couldn't find out. A valid and important terminus — document it *specifically*: which sources were searched, with what queries, and what came back. "We searched the issue tracker for A and B, read the 6 PRs touching this file, and pickaxed the constant; none surfaced a rationale" beats "we don't know."

## Phrasing Guide

**Words that carry confidence** — only for Direct/Supported, with a citation adjacent: "because", "the reason is", "was designed to", "fixes/solves", "the maintainers decided".

**Words that hedge** — for Inferred and below: "appears to", "seems to", "likely", "suggests", "is consistent with", "one reading is", "may have been".

**Words to avoid**: "obviously" / "clearly" / "of course" (if it were, no one would be asking); "just" (dismissive, hides uncertainty); "I think" (use "the evidence suggests").

## Avoid Rationalization

Code that "makes sense" today may have been written for reasons that no longer apply, or for no good reason at all. Resist:

- Assuming the author did the "right" thing and working backward to justify it
- Assuming a consistent pattern was intentional when it might be copy-paste
- Turning absence of evidence into evidence of absence ("no one mentioned security, so it wasn't a concern")

## The Sycophancy Trap

"Why is it this way — I assume for performance?" embeds a hypothesis. Don't confirm it because it was offered; treat it as one candidate and check the evidence independently. The user's guess is a prompt for investigation, not a conclusion to validate. (In a study dialogue this matters double: confirming a wrong guess installs a durable misconception.)

## When Evidence Contradicts

If two sources disagree (PR says "tech debt cleanup", issue says "customer compliance requirement"), surface both with citations. Both may be true at different levels; don't pick the tidier narrative.

## Calibration Check

Before accepting an interpretation (the user's or your own), ask:

1. Does each claim have a citation? If not, it drops to Inferred or below.
2. Is the phrasing calibrated to the tier? (Direct may say "because"; Inferred may not.)
3. Is the code being treated as evidence of its own intent? That's not evidence — reclassify.
4. Were any gaps named? An interpretation with no unknowns is suspicious — either the record was unusually complete, or something is being papered over.
