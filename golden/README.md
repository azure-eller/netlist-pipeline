# Golden set

Boards with expected scorecards for gating judge versions (SPEC.md, "Judge versions and
approval"). Built by `scripts/golden.py --capture` from local runs; rerunning overwrites.
`expected.json` is the rule judge's answer (regression and disagreement, not ground truth).

| case | board | netlist / constraints | expected (rules 0.1.0) |
|---|---|---|---|
| `pic_human` | `tests/fixtures/pic_programmer/pic_programmer.kicad_pcb`, the human layout | latest local judge-mode run of pic_programmer | -6.48; 3 decoupling (C1 on VCC, C6 and C7 on VCC_PIC) |
| `pic_generated` | chosen candidate of local generate run 34 (3 seeds) | same as `pic_human` | -1.20; decoupling C1, current on VCC_PIC |
| `rpi_generated` | chosen candidate of local generate run 27 (rpi_hat) | run 27 | -3.43; 2 current (+5V, GND) |
| `pic_cap_far` | `pic_human` with C1 moved 30 mm right of U1 (`pcb.set_positions`) | same as `pic_human` | -16.12; same 3 decoupling, C1 now ~93 mm from the nearest VCC pin. Must score below `pic_human`. |
| `rpi_thin` | `rpi_generated` with every `+5V` segment width set to 0.15 mm (text edit) | run 27 | -4.99; same 2 current violations, +5V excess larger. Must score below `rpi_generated`. |

Note: C1 already violates decoupling in the human layout (11.8 mm from U3 pin 5 on VCC; U1
carries no VCC pad), and the one `+5V` segment on the HAT already violates current at 0.2 mm.
The two mutated cases therefore worsen an existing violation rather than adding one; the
ordering constraints are what the gate checks.

## Impedance-live cases (added 2026-09-08)

The five original cases never exercise the impedance rule: the fixtures' fast nets carry no
copper. Two cases fix that, both on the generated HAT board with its routed I2C nets declared
`high_speed`:

- `rpi_fast`: target 50 ohm. A 0.2 mm trace over 1.51 mm FR-4 is far from 50 ohm, so both
  nets violate under every provider; the score depends on the provider's z0 (oracle -20.58,
  closed form -20.88).
- `rpi_fast_tuned`: target set to the closed form's own answer for that geometry. The formula
  passes by construction; the oracle passes only if its z0 is within 10% of the formula's,
  which it is here. A surrogate that drifts fails this case first.

Expected answers for every case now come from the oracle (`scripts/golden.py --physics
oracle --update`); the closed form is measured against them like any other judge and passes.

## Answer key recaptured from the general solver (2026-09-09)

`scripts/golden.py --physics oracle --update` again, now with the impedance rule asking the
provider per real cross-section (`docs/FACTORY.md` step 2: neighbours, plane or not). The five
plane-less cases did not move. The two impedance-live cases did, because the generated HAT has
no plane under its I2C traces and the old key assumed one:

| case | old key (plane assumed) | new key (real cross-sections) |
|---|---|---|
| `rpi_fast` | -20.58 | -24.18 |
| `rpi_fast_tuned` | -3.43 | -4.16 |

Any judge that still assumes a plane (the closed form, `learned-fd v5`) is measured against
this key like any other; the ledger in `docs/experiments/golden.jsonl` records what happened.
