# Workflow script skeletons

Reach for a script when control flow matters: retries, barriers, budgets, more than ~5 units, or any flow that will run again. The script is the process made executable — order, deduplication, and finding-routing live in code here, so no node carries them.

Every `agent()` call sets `opts.model` explicitly (Workflow inherits the main-loop model otherwise, bypassing the routing table).

## Scout, then pipeline

Discover the work-list inline (cheap), then run each item through its stages with no barrier between items.

```javascript
export const meta = {
  name: 'sweep',
  description: 'Discover units, run each through fix and verify',
  phases: [{ title: 'Fix' }, { title: 'Verify' }],
}
// scout was done inline before invoking; the list arrives via args
const results = await pipeline(
  args.units,
  u => agent(`<brief for ${u}>`, { label: `fix:${u}`, phase: 'Fix', model: 'sonnet' }),
  (fix, u) => agent(`Verify by artifact: ${u}. Claim: ${JSON.stringify(fix)}`,
                    { label: `verify:${u}`, phase: 'Verify', model: 'opus', schema: VERDICT }),
)
return results.filter(Boolean)
```

## Evaluator loop (builder × oracle)

Acceptance is built before implementation; the loop runs until the oracle's execution says pass. Cap iterations — an uncapped loop hides a spinning one.

```javascript
export const meta = {
  name: 'build-to-oracle',
  description: 'Oracle-first acceptance, builder iterates until green',
  phases: [{ title: 'Acceptance' }, { title: 'Build' }],
}
const oracle = await agent(`Build the executable acceptance oracle for this spec: ${args.spec}.
Return the artifact path and rerun command.`, { phase: 'Acceptance', model: 'opus', schema: ORACLE })
let verdict = null
for (let i = 0; i < 4 && verdict?.pass !== true; i++) {
  const build = await agent(`Implement to pass ${oracle.path} (never modify it). Brief: ${args.brief}`,
                            { phase: 'Build', model: 'sonnet' })
  verdict = await agent(`Run ${oracle.rerun}; report pass/fail with output.`,
                        { phase: 'Build', model: 'haiku', schema: PASSFAIL })
}
return { verdict, oracle: oracle.path }
```

## Panel with mechanical routing

Diverse finders in parallel; findings split by falsifiability — executable claims go to oracle verdicts, judgment claims go unfiltered to the owner.

```javascript
export const meta = {
  name: 'panel-review',
  description: 'Diverse lenses, dedupe, route findings by falsifiability',
  phases: [{ title: 'Find' }, { title: 'Verify' }],
}
const found = (await parallel(args.lenses.map(l => () =>
  agent(`<charter: ${l}> Review ${args.target}.`, { label: l, phase: 'Find', model: 'opus', schema: FINDINGS }),
))).filter(Boolean).flatMap(r => r.findings)
const deduped = dedupeByFileLine(found)                      // plain code, not an agent
const verdicts = await parallel(deduped.filter(f => f.falsifiable).map(f => () =>
  agent(`Operationalize and run: ${f.failure_scenario}`, { phase: 'Verify', model: 'opus', schema: VERDICT })
    .then(v => ({ ...f, verdict: v })),
))
return {
  confirmed: verdicts.filter(Boolean).filter(f => f.verdict.confirmed),
  for_owner: deduped.filter(f => !f.falsifiable),             // judgment claims: unfiltered, owner adjudicates
}
```
