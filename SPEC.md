# netlist-pipeline specification

This file is the contract. Code that disagrees with it is wrong; if the spec is wrong, change
the spec first, in the same change.

## Purpose

A backend that takes a customer's KiCad schematic, optionally with their laid-out board, and
produces a physics-judged, independently verified, fabrication-ready board. Every stage is a
recorded job with tool version, input hash, and output hash, so any artifact traces back to
what produced it.

The physics judge is a slot. The reference implementation is rule-based (`pipeline/judge.py`).
A learned model replaces it behind the same HTTP contract and nothing else changes.

## Two modes, one chain

| Mode | Upload | Stages |
|---|---|---|
| `judge` | zip with `.kicad_sch` (+ `.kicad_pro`) and a `.kicad_pcb` | extract_netlist, constraints, build_board, judge, verify, export |
| `generate` | `.kicad_sch` alone, or a zip without a board (or `?mode=generate`) | extract_netlist, constraints, build_board, place, route, judge, verify, export |

Mode defaults to `judge` when the upload contains a `.kicad_pcb`. The netlist is never
uploaded; it is derived from the schematic. A `constraints.json` in the zip supplies what the
files cannot (see Constraints).

## Stages

| # | Stage | Reads | Writes | Tool of record |
|---|---|---|---|---|
| 1 | extract_netlist | upload | `runs/<id>/netlist.json`; `components`, `nets`, `net_nodes` rows; ERC counts in stage details (recorded, not a gate) | `kicad-cli sch erc`, `kicad-cli sch export netlist --format kicadsexpr` |
| 2 | constraints | `.kicad_pro`, net names, `constraints.json` | `constraints` row (body + per-field source) | pipeline |
| 3 | build_board | upload, netlist, constraints | `runs/<id>/unplaced.kicad_pcb` (generate) or the supplied board as candidate seed 0 at `runs/<id>/candidates/0/board.kicad_pcb` (judge) | pcbnew |
| 4 | place | unplaced board, constraints | `.../placed.kicad_pcb`, `candidates.proxy_cost` | pipeline (simulated annealing) |
| 5 | route | placed board | `.../routed.kicad_pcb`, unrouted count | Freerouting via Specctra DSN/SES |
| 6 | judge | candidate board, netlist, constraints | `candidates.score`, `.metrics`, `runs/<id>/report.json`; best candidate `chosen` | `judge.py` or `JUDGE_URL` |
| 7 | verify | chosen board, netlist | `verifications` row; run `failed_verification` on failure | `kicad-cli pcb drc --schematic-parity`, `kicad-cli pcb export ipcd356` |
| 8 | export | chosen board | `artifacts`: `gerbers.zip`, `drill.zip`, `positions.csv`, `stats.json`, `board.png`, `report.json`, `board.kicad_pcb` | `kicad-cli pcb export ...` |

Any stage failure marks the stage and the run `failed` with the error text and stops the chain.
Jobs retry up to three times on worker crash (stale `running` jobs are requeued after
`JOB_TIMEOUT_SECONDS`); a stage exception does not retry.

## Contracts

- **Connectivity comes from kicad-cli.** Nets are never derived from the drawing.
- **Uploads are content-addressed.** Key `designs/<sha256>/<filename>`. Same bytes, same
  design row, new run.
- **API handlers never do work over a second.** Everything else is a job.
- **Jobs are Postgres rows** claimed with `FOR UPDATE SKIP LOCKED`.
- **Every stage records provenance** in `stages`: tool, tool_version, input_hash, output_hash.
- **Schema changes are forward-only migrations** in `migrations/NNN_name.sql`.
- **The judge never gates export on its own.** Verify does. Judge picks the best candidate;
  verify decides whether the run passed.

## Constraints

Derived in order, later sources override earlier, and each field records its source:

1. Defaults (`models.Constraints`).
2. `.kicad_pro`: net classes and their members, design rules, stackup when present.
3. Net-name patterns: power (`GND*`, `VCC*`, `VDD*`, `+3V3`, `+5V`, `VBUS`, ...),
   high_speed (`*CLK*`, `USB*`, `*MISO*`, `*MOSI*`, `*SCK*`, `*TX*`, `*RX*`, `D+`, `D-`),
   diff pairs (`X_P`/`X_N`, `X+`/`X-`).
4. `constraints.json` in the upload:

```json
{
  "classes": {"USB_D+": "diff", "USB_D-": "diff", "CLK": "high_speed"},
  "diff_pairs": [["USB_D+", "USB_D-"]],
  "rail_current_a": {"+3V3": 0.5},
  "impedance_ohm": {"high_speed": 50, "diff": 90},
  "fixed": {"J1": [10.0, 20.0, 0]},
  "footprints": {"R1": "Resistor_SMD:R_0603_1608Metric"},
  "outline_mm": [60, 40],
  "stackup": {"layers": 2, "board_thickness_mm": 1.6, "copper_um": 35, "dielectric_mm": 1.51, "er": 4.5},
  "decoupling_max_mm": 10
}
```

Validation: every net named exists in the netlist; every ref in `fixed` and `footprints`
exists; `footprints` supplies "Lib:Name" for parts whose schematic symbol has none; class names
are one of `default`, `power`, `high_speed`, `diff`. Invalid input fails the stage.

## Judge contract

```
POST /v1/score
Authorization: Bearer <JUDGE_TOKEN>
{"run_id": 1, "netlist": {...}, "constraints": {...}, "board_pcb": "<.kicad_pcb text>"}

200 {"score": -12.5, "metrics": {"nets": {...}, "rails": {...}, "totals": {...}},
     "violations": [{"rule": "impedance", "net": "CLK", "measured": 62.1, "threshold": 50,
                     "message": "..."}],
     "judge": {"name": "rules", "version": "0.1.0"}}
```

Score is 0 for a board with no violations and decreases with each violation's weighted
excess; higher is better. Rules in the reference judge: single-ended and differential
impedance, diff-pair length mismatch, decoupling capacitor distance to the IC power pin it
serves, trace current capacity (IPC-2221) against rail current, crosstalk proxy (parallel run
length over spacing), via count on high-speed nets, unrouted count.

The impedance rule asks the physics provider (`pipeline/physics.py`) for each high-speed net's
`z0` through its real cross-sections: the net's window is cut every millimetre
(`windows.cuts`), the provider's `cut(cut)` answers per cut or `None` when the cut has no
reference (no plane and no neighbour), and the net's `z0` is the median of the answers. A net
with no reference anywhere falls back to the ideal-trace call `z0(w, h, t, er)` on its
narrowest segment. `metrics.nets[net]` records `reference: "cut" | "assumed"`, `n_cuts` and
`n_with_reference`, so a verdict says whether its physics saw the board or guessed a plane.

### Judge versions and approval

A judge version reaches the worker only after it agrees with the golden set.

- **Golden set** `golden/<case>/{board.kicad_pcb, netlist.json, constraints.json,
  expected.json}`: five boards with expected scorecards, captured once from local runs by
  `scripts/golden.py --capture` (see `golden/README.md` for what each case is). `expected.json`
  is `{score, violations: [{rule, net, ref}], totals}` from the reference rule judge. Expected
  answers are the rule judge's answers, not ground truth: the set catches regressions and
  disagreements, it does not prove a judge right.
- **Run** `make golden` (in-process rules judge) or `scripts/golden.py --judge-url URL`
  (`POST URL/v1/score` per case, bearer `JUDGE_TOKEN`). A remote judge reports itself at
  `GET URL/v1/info -> {name, version, capabilities, artifact_sha256}`; a 404 there is treated as capabilities
  `["score", "violations"]` and version unknown, and is printed as such.
- **Pass** iff every case's score is within `max(0.5, 10% of |expected|)`; the violation set
  equals expected on `(rule, net, ref)` when `capabilities` includes `"violations"` (a
  score-only learned judge is not held to violations); and, always, `pic_cap_far` scores below
  `pic_human` and `rpi_thin` scores below `rpi_generated`. Every run prints a table and appends
  one line to `docs/experiments/golden.jsonl` (`{ts, judge, version, judge_url, passed, cases}`);
  exit 1 on failure.
- **Registry** a served judge is a bundle, and the `models` row is what pins it: `name`,
  `version`, `dataset_id` (which names sampler and solver versions and the seed),
  `artifact_key` (`models/judge/<version>-<sha12>.joblib`), `sha256` of the bytes, and
  `metrics` carrying `feature_version` (the encoding, `surrogate.FEATURE_VERSION`), the
  scikit-learn version and the training commit. `models` and `judge_approvals` rows refuse
  update and delete (migration 005); a retrain is a new version. The judge service loads the
  key the row names and refuses bytes whose sha256 differs from the row, and a surrogate whose
  `feature_version` differs from the code's.
- **Approve** `scripts/golden.py [--judge-url URL] --approve` inserts one `judge_approvals`
  row `{name, version, artifact_sha256, golden_sha, approved_at}`, only when passing.
  `artifact_sha256` is what `/v1/info` reported (null for the in-process rules judge);
  `golden_sha` is the sha256 over all `expected.json` files in path order, so an approval
  names the bytes and the set they passed. `--update` rewrites `expected.json` from the
  current judge; explicit, never automatic.
- **Gate** with `JUDGE_URL` set, the judge stage calls `GET /v1/info` once per stage and
  fails the run unless a `judge_approvals` row matches the reported `(name, version,
  artifact_sha256)` and, when `JUDGE_VERSION` is not `rules`, the version equals
  `JUDGE_VERSION`. The error names the offending name, version and artifact. The stage records
  `{name, version, artifact_sha256}` in its details and the judge's `report.json` carries the
  same, so every verdict names the bytes that made it. The in-process rule judge is the
  reference and is not gated.

## Placers

`POST /designs?placer=search|claude` (default `search`) chooses how generate mode places parts.

- `search`: seeded simulated annealing over the proxy cost (wire length, decoupling distance,
  1.0 mm courtyard clearance, bounds). Deterministic per seed.
- `claude`: the search layout for each seed is handed to Claude (Agent SDK, model
  `claude-fable-5-1`, no tools, one turn, structured output) as a compact board description;
  the proposal is validated in code (movable refs only, rotations snapped, positions clamped
  inside the outline, fixed and mechanical parts restored) and written as the candidate. The
  search board is kept beside it as `candidates/<seed>/placed-search.kicad_pcb`. Router, judge
  and verification are identical. Every proposal, its rationale, tokens and costs are recorded
  under `docs/experiments/claude-placer/` when `EXPERIMENTS_DIR` is set. This placer is an
  experiment with a row in the eval table, never a silent fallback: if the SDK call fails, the
  stage fails.

## Physics: oracle, datasets, surrogate

The judge's rules never change; where its impedance numbers come from does
(`pipeline/physics.py`):

Every provider answers three calls: `z0(w, h, t, er, inner)` and `zdiff(w, s, h, t, er, inner)`
for an ideal trace over a plane, and `cut(cut)` for a real cross-section.

| provider | name | what it is | `cut` | speed |
|---|---|---|---|---|
| closed form | `rules 0.1.0` | IPC-2141 formulas, the original reference | target alone, plane assumed | microseconds |
| oracle | `oracle-fd fd2d-0.1+fd2d-cut-0.1` | `pipeline/fields.py`: `solve` (one trace or a pair over a plane) and `solve_cut` (any number of conductors on a layer, plane or not, full capacitance matrix by the charge on each conductor), validated against the Hammerstad-Jensen and coplanar-waveguide closed forms | the general solver | under a second per cut |
| surrogate | `learned-fd <version>` | gradient boosting on `solve`-labelled geometries | target alone, plane assumed | under a millisecond |
| cut model | `learned-cut <version>` | `pipeline/cutnet.py`: a transformer over the cut's conductors predicting both capacitance matrices; `z0` and coupling by `fields.derive`, the solver's own formula | the model | milliseconds |

**Datasets** (`pipeline/data.py`, `scripts/dataset.py`, tables `datasets`, `dataset_shards`):
`make dataset [KIND=cut] SHARDS=n N=m SEED=s` samples cross-sections from ranges seen on real
boards (`cross-section-0.1`: one trace or a pair; `cut-0.1`: a target with 0-4 neighbours and
a plane half the time), solves them with the matching solver in `data:shard` jobs through
the same queue as everything else,
writes JSONL shards to `datasets/<id>/shard-NNN.jsonl` and a manifest with sampler version,
solver version, seed, per-shard sha256 and counts. A dataset is `ready` only when every shard
is present and hashed. Regenerating a shard is `--only-shard`.

**Models** (`scripts/train_surrogate.py`, `scripts/train_cutnet.py`, table `models`): read a
ready dataset by manifest, train, report held-out error, error on fresh solver-labelled
geometries and (cut model) error on the real cuts of every board family, marking which
families were trained on, upload `models/judge/<version>-<sha12>.joblib` with the dataset id,
solver, sampler and feature versions, library version, commit and metrics inside the
artifact, and insert a `models` row. `JUDGE_VERSION=<version> make judge` serves
it; `JUDGE_VERSION=oracle` serves the oracle itself.

**Truth for the gate**: `scripts/golden.py --physics oracle --update` rewrites the golden
set's expected answers from the oracle. A surrogate is approved only if it agrees with the
oracle on those boards within tolerance; the closed form is measured against the oracle the
same way.

## Data factory (docs/FACTORY.md)

Where a physics model's labels come from. Step 1 is in place; the roadmap and status lines
live in `docs/FACTORY.md` and move in the same change as the code.

**Window** (`pipeline/windows.py`, `WINDOW_VERSION`): one net plus everything within
`radius_mm` (5) of the midpoint of its longest segment, with the board's copper layers and
stackup. Contents: every segment meeting the box (clipped to it, net kept), every via and
copper pad meeting it, every copper pour's outline clipped to it (fills are not stored in
KiCad 10 files; the outline counts as filled). Coordinates are re-origined at the box corner
and rounded to 1 µm; `geometry_hash` is the sha256 of the canonical JSON without the origin,
so the same local geometry anywhere on any board hashes the same. Orientation is not
normalized.

**Cut**: at points every 1 mm along the target net's segments (at least 3 per net), the line
perpendicular to the segment: every conductor on that layer the line crosses as
`(offset, width, net)` with the target at offset 0 (same-net overlaps such as a segment ending
in its pad merge), `plane_below` / `plane_above` (a pour on the adjacent copper layer contains
the point), and `h`, `t`, `er` from the stackup (one dielectric height for every layer).

**Label** (step 2, `fields.solve_cut`, `CUT_SOLVER_VERSION`): every conductor of the cut
solved together (the nearest six neighbours kept), the plane as ground when either flag is
set, air below the slab otherwise. Stored per cut: `z0` (target driven, everything else at
0 V), `eps_eff`, `c11_pf_per_m`, `coupling` (net, k) per neighbour, both capacitance matrices,
target index, conductor count, plane flag. `None` with no plane and no neighbour. A window
row labelled by an older solver version is relabelled in place by the next `data:windows`
job; `data.load_windows` returns every cut with its label and board family.

**Tables**: `boards` (source, path, sha256 unique, object key `boards/<sha>.kicad_pcb`,
family = split unit, layers, nets with copper, stackup) and `windows` (board, net, radius,
window version, geometry hash unique, object key `windows/<hash>.json` holding window, cuts
and labels, conductor and cut counts, labels with per-cut params and `z0_mean/min/max` over
cuts with a plane, solver version, seconds). Job `data:windows {board_id}` makes one window
per net with copper and skips hashes already stored under the current solver version, so
rerunning inserts nothing. `scripts/factory.py add-runs` registers every routed candidate of
every run as a board (`source = 'run'`, family = the design it came from).

## Verification (independent of the judge)

Passed iff all of: DRC with schematic parity reports zero errors, excluding silkscreen rules
(`silk_*`: cosmetic, fabs clip silk over copper; counted separately in the summary); the IPC-D-356 netlist
exported from the board equals the schematic netlist (net name -> set of `ref.pin`, names
normalized: leading `/` stripped, case preserved); unrouted count is 0; every footprint's
pads lie inside the outline (courtyards may overhang: mounting holes and edge connectors do).

## Invariants (tested)

1. Every `net_nodes.ref` exists in `components.ref` for the same design.
2. Every net has at least one node.
3. Re-uploading identical bytes creates no new design row.
4. A failed stage leaves the run `failed` with the error text and no later stages.
5. In generate mode, `verify` on the chosen candidate reports `netlist_match = true` for the
   fixtures; a board with a pad moved to another net reports `false`.

## Definition of done

`make check` passes: ruff, mypy strict, unit tests, then the end-to-end suite against the
compose services (judge mode on pic_programmer, generate mode on rpi_hat, and the negative
verification cases). Report its output, not a claim.
