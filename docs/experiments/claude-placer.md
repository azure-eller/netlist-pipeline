# Claude as a placement refiner

## Question

Does one Claude call, given the annealed layout and the design's structure, produce a
placement that routes, passes DRC, and scores better on the physics judge than the search
alone? The search optimises a proxy (wire length, decoupling distance, overlap); the model
sees the same proxy plus the netlist, so it can trade proxy cost for things the proxy does not
model (rail topology, connector edges, thermal). We measure both.

## Setup

- Run: `POST /designs?mode=generate&seeds=N&placer=claude`. Stage `place` runs the annealer
  per seed exactly as before, then calls `placer_claude.refine` once per seed and writes the
  refined board as the candidate. The search board is kept at
  `candidates/<seed>/placed-search.kicad_pcb`.
- Call: `claude-agent-sdk` `query(...)`, model `claude-fable-5-1`, effort `high` (the smoke
  test used `low`), `tools=[]`, `max_turns=1`, JSON-schema structured output
  `{positions: [{ref, x, y, rot}], rationale}`. Subscription auth; no API key.
- Prompt (`placer_claude.build_prompt`): goal and pass criteria, outline, every footprint
  (value, footprint, courtyard size, current pose, fixed or not), nets by class with member
  pins (default class capped to the 40 largest), decoupling pairs with current distance, the
  search layout's proxy cost, and the constraints (fixed parts stay, rotations 0/90/180/270,
  1.0 mm courtyard clearance, inside the outline, overlaps or unrouted nets score zero).
- Validation (`placer_claude.validate`): only movable parts may move; rotation snapped to
  90 degrees; courtyard clamped inside the outline; unknown refs ignored. No overlap repair:
  an overlapping proposal is scored as proposed, so the proxy cost and the router show it.
- Provenance: tool `claude-agent-sdk`, version = model id, input hash over the search boards.
- Records: `docs/experiments/claude-placer/<fixture>-run<id>-seed<n>.json` (prompt size,
  usage, seconds, raw output, moved and rejected refs, search and refined proxy cost). Written
  only when `experiments_dir` is set (local; unset on Render).
- Eval: `scripts/eval.py --placer claude --note "..."` adds `<fixture> / claude xN (generate)`
  rows to `docs/EVAL.md` with the search -> refined proxy cost next to the judge score.

Same judge, same verification, same seeds as the search rows, so the rows are comparable.

## Results

_To be filled by the eval run._

| fixture | seed | search cost | refined cost | moved | tokens in/out | s | verified | score |
|---|---|---|---|---|---|---|---|---|

### Per-seed observations

### Verdict
