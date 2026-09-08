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
| low | 1 | 20 min | returned in 185 s, 14830 output tokens; moved all 56 movable parts; proxy cost 5306 -> 36779; 16 unrouted, 87 DRC errors, failed verification; judge -30.0 vs search -1.20 | 67 |

The prompt for this board is 9.2k characters (63 movable parts, 111 nets; the small board is 2.3k). At high and medium
effort the model had not returned a structured layout within the cap. The record of each
attempt is the run's stage rows; no proposal file exists because none was produced.

### Verdict

- **Small board (6 parts):** Claude refines the search layout into something equally good.
  Every seed routes and verifies, the judge cannot tell the two apart, and the proxy cost is
  1-2% worse. The rationales show real circuit reading (pins facing the header they connect
  to, pull-ups between header and EEPROM, decoupling next to VCC). It costs 40-80 s and
  3-6k output tokens per seed against 40 ms for search, for no measurable gain.
- **Large board (63 parts):** at high and medium effort no proposal came back within the
  cap; at low effort the proposal came back in three minutes and was far worse than search:
  seven times the proxy cost, sixteen unrouted nets, failed verification. A language model
  placing 56 parts in one shot from a text description does not respect geometry it cannot
  see; search with an overlap term does.
- **What the experiment does say:** the pipeline can measure a placer in an afternoon, with
  the same router, judge and verification for every configuration and a JSON record per
  proposal. What it does not say: whether a tool-using Claude that can run the router and
  DRC in a loop would do better. That is a different experiment, and the harness for it is
  this one plus a tool server.
- Low-effort rationale, first lines: "Kept P3 (40-pin socket, unknown courtyard origin) where the annealer left it and organized everything else into functional clusters around it. Left of P3: LT1373 boost converter (U4, L1, D10, C3, R10, C5, C4, RV1, R15, R16) with C1 placed directly under U4 so its VCC pin is ~11 mm away (was 18). Right of P3, top band: VPP switch (Q2, R7, C9, R11, Q1, D11, R8, R17/R18/R9, D8) sitting directly above the PIC sockets it drives; top-right corner: power entry P1 -> D1 -> C2 -> 7805 as a straight short chain. Middle: U5/U1/U6 socket group with C6/C7 within ~5 mm of U1.8 on VCC_PIC, P2 below them. Serial front end laid out as a pipeline J1 -> R1..R6 columns -> D2..D7 columns -> U2 -> R12/R13 -> sock"

Files: `claude-placer/rpi_hat-run64-seed{1,2,3}.json`, `claude-placer/pic-run67-seed1.json`,
`claude-placer/smoke-rpi_hat-seed1.json`.
