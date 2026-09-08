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

Run 64, 2026-09-08, model `claude-fable-5-1`, effort high, short system prompt, three seeds.
Same router (Freerouting, 20 passes), same rule judge, same verification as the search rows in
`../EVAL.md`. Search's own result on this board: all seeds route, verified, score -3.43.

| fixture | seed | search cost | refined cost | moved | tokens in/out | s | verified | score |
|---|---|---|---|---|---|---|---|---|
| rpi_hat (6 parts) | 1 | 666 | 680 | 5 | 45872/3121 | 42 | yes | -3.43 (= search) |
| rpi_hat (6 parts) | 2 | 675 | 679 | 5 | 45872/6546 | 84 | yes | -3.43 (= search) |
| rpi_hat (6 parts) | 3 | 666 | 675 | 5 | 45872/2737 | 38 | yes | -3.43 (= search) |

### Per-seed observations (rpi_hat)

- Every seed moved all five movable parts and every seed routed fully (0 unrouted) and
  verified clean (0 DRC errors, netlist match, in bounds).
- The judge score is identical to search's on all three seeds: the only violations on this
  board are the two 0.2 mm power traces against the 1 A rail requirement, which placement
  cannot change. The rule judge has no term that a better placement of six parts would move.
- The proxy cost got 1 to 2 percent worse on every seed. Claude spent that on clearance: each
  rationale talks about keeping the cluster clear of the header's courtyard, and the search
  layout was already at the 1.0 mm clearance the placer enforces.
- The rationales read like an engineer: seed 2 rotated the EEPROM so its SDA/SCL/WP/VCC side
  faces the header pins 27/28 it connects to, put the SCL pull-up on the 3V3 side nearest
  header pin 17, and kept the decoupling cap 2.7 mm from the VCC pin. Seed 3 judged the search
  layout "already sound" and shifted the cluster 3 mm for clearance. That is real circuit
  reading; it produced no measurable difference here.
- Cost per seed: 38 to 84 s and 2.7k to 6.5k output tokens against 40 ms for search.

### pic_programmer (63 parts)

| effort | seeds | cap per seed | outcome | run |
|---|---|---|---|---|
| high | 3 | none | seed 1 still generating after 23 min; killed | 61 |
| medium | 3 | 20 min | seed 1 hit the cap; stage failed with TimeoutError | 66 |
| low | 1 | 20 min | see below | 67 |

The prompt for this board is 9.2k characters (63 movable parts, 111 nets; the small board is 2.3k). At high and medium
effort the model had not returned a structured layout within the cap. The record of each
attempt is the run's stage rows; no proposal file exists because none was produced.

### Verdict
