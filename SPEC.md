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
`JOB_STALE_AFTER_SECONDS`); a stage exception does not retry.

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
impedance (IPC-2141 microstrip), diff-pair length mismatch, decoupling capacitor distance to
the IC power pin it serves, trace current capacity (IPC-2221) against rail current, crosstalk
proxy (parallel run length over spacing), via count on high-speed nets, unrouted count.

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
