# The data factory

Where a physics model's labels come from. Read this before touching anything under it, and
update the status lines in the same change that moves a step.

## Why

Origamics' researcher listing: models that "learn how signals, power, and EM fields actually
behave on a board", built with "geometric deep learning", and "the data pipelines, simulation
environments, and evaluation methodology that let us know a model is actually right". The
serving side of that (judge slot, golden gate, versioned artifacts, queue) exists in this repo.
The factory is the other half: turn boards into labelled geometry, at volume, with provenance,
so a geometry model can be trained and gated.

The unit is the **window**: one net plus everything within a radius, with the stackup
(`pipeline/windows.py`). Signal-integrity physics is local, so a window is small enough for a
solver to label in well under a second and is what a geometry model consumes. Every later step
keys on it: harvesting produces boards to window, fidelity tiers label windows, edits make child
windows, datasets are lists of windows, the model consumes windows, the active-learning loop
ranks windows.

Not attempted here, said plainly: open 3D full-wave solvers (openEMS is not packaged for this
machine, Palace needs an MPI build), fabricated coupons and bench measurement, and free
rotate/mirror augmentation (the identity for a cross-section label).

## Steps

1. **Windows and cuts from the boards we have.** `done` (this repo, `windows.py`,
   `data:windows`, `scripts/factory.py`, migration 004). One window per net with copper,
   centred on the net's longest run; perpendicular cuts every millimetre listing every
   conductor the cut line crosses and whether a pour sits on the adjacent layer; label = the
   existing microstrip solver on the target conductor alone, `None` without a reference plane.
   Neighbours are recorded, not solved.
2. **General cross-section solver.** `done` (2026-09-09, `fields.solve_cut`,
   `CUT_SOLVER_VERSION fd2d-cut-0.1`). N conductors on one layer, the plane as ground when
   present and air below the slab when not, every conductor driven in turn, the capacitance
   matrix from the charge on each conductor (one factorisation, N right-hand sides), with the
   dielectric and with air; target `z0` with everything else at 0 V and capacitive coupling
   to each neighbour by net (`fields.derive`). Validated in `tests/test_fields.py`: reduces
   to `solve` for one trace and for a pair's even/odd modes, matches the thickness-corrected
   coplanar-waveguide closed form for the plane-less path within 3 %, matrix symmetric,
   charge and energy agree, far neighbours do not matter, coupling falls with gap, grid
   converged. Not modelled: conductors on other layers (a pour on the adjacent layer is the
   plane; a trace there is not seen), vias, losses, frequency. Windows are relabelled under
   it; `windows.label` calls it.
3. **Board supply.** `partial` (2026-09-09: `scripts/factory.py add-runs` registers every
   routed candidate of every run, 35 boards from 38 candidates, families `pic_programmer`
   and `rpi_hat`; two families only, so the family split is one whole family held out).
   Still to do, `scripts/harvest.py github`: `gh api search/code
   extension:kicad_pcb`, keep KiCad 6+ files (the syntax `board.parse` reads), keep only repos
   with an OSI license and record it, family = repo, dedup by file sha256 and geometry hash,
   boards that fail to parse recorded with the error. Fixtures and every verified generate-mode
   candidate in `runs` as sources too (procedural realism from our own placer and router).
4. **Fidelity tiers.** `later`. `recipes(name, tier, solver, solver_version, params)` frozen
   rows: `t0-formula` (closed form on the target only), `t1-fd-128`, `t2-fd-512` (the check
   tier, finer grid and wider margin). `labels(window_id, recipe_id, values, seconds, status)`
   replaces `windows.labels`. Plausibility gate (z0 5–300 Ω, coupling 0–1), every 20th `t1`
   window also solved at `t2` (convergence spot check), a review query for tier disagreement
   over 25%, seconds per label per recipe. Tier 3 (openEMS/Palace) and 4 (measurement) are
   recipe rows a runner could serve; none built.
5. **Edit pairs.** `later`. `windows.edit(window, kind, rng)`: `scale_width`, `stackup`,
   `move_neighbour`, `drop_plane`; children carry `parent_id` and `edit`; a pair is two labels
   joined through `parent_id`. Deterministic from (parent hash, kind, seed).
6. **Frozen datasets, baseline, FNO, geometry-native judge.** `partial` (2026-09-09). Built:
   the cut sampler (`data.sample_cut`, `cut-0.1`: target, 0-4 neighbours, plane half the
   time) and `make dataset KIND=cut`; `pipeline/cutnet.py`, a transformer over the cut's
   conductors (pairwise-gap attention bias, symmetric pair head) predicting both capacitance
   matrices, trained by `scripts/train_cutnet.py` on synthetic cuts plus, optionally, the
   real cuts of named families, and reported on held-out synthetic, fresh solver-labelled
   cuts, and the real cuts of every family with the never-trained-on family as the transfer
   test; the gradient-boosting baseline on hand features (`--model gbr`); every provider's
   `cut` call and the judge's cut-based impedance rule; the golden answer key recaptured
   from the general solver. Results in `experiments/cutnet-v6.md`. Not built: a frozen
   `windows` dataset kind with a card, the field-predicting operator (geometry in, voltage
   map out, Laplace residual in the loss; the solver already produces its labels), and a
   `Physics.cut` for the crosstalk rule. The original plan, kept for the record:
   `datasets.kind = 'windows'`: a frozen list of window hashes with labels under one recipe, split by
   `sha256(family, split_seed)` so a board family is entirely train or held out, plus a card
   (counts by source, layers, width and spacing bins, plane present, edits, labels per tier,
   solver seconds). `scripts/train_window.py --model gbr|fno`: gradient boosting on cut
   features as the baseline; a Fourier Neural Operator (PhysicsNeMo, RTX 5070) on cut rasters
   (permittivity map, conductor mask) predicting the potential field, `z0` from the field's
   energy as the solver does. Both report held-out-family error and fresh-oracle error, write
   `models` rows, and are gated by `scripts/golden.py` through a `Physics.cut` provider
   method; the judge's impedance rule extracts windows and cuts for each high-speed net, so
   every provider becomes geometry-native.
7. **One active-learning cycle.** `later`. Ensemble of three baselines, rank `t1`-only
   windows by spread, escalate the top N to `t2`, coverage histogram with the emptiest bins
   named, retrain, before/after numbers in `docs/experiments/factory-cycle-1.md`.

## Fixed knobs, and why they are versioned

| knob | value | recorded on |
|---|---|---|
| window radius | 5 mm | `windows.radius_mm` |
| cut step, minimum cuts | 1 mm, 3 | `WINDOW_VERSION` |
| window layout (what is in the box, rounding, centre rule) | `window-0.1` | `windows.window_version` |
| solver, one trace or a pair | `fd2d-0.1` | `datasets.solver_version` |
| solver, general cut | `fd2d-cut-0.1` | `windows.solver_version`, `datasets.solver_version` |
| cut sampler | `cut-0.1` | `datasets.sampler_version` |
| cut model features | `cut-features-0.1` | the artifact, checked at load |

A change to any of them changes what a sample is. Bumping the version and leaving old rows in
place is how a later dataset can say which samples it was built from; silently changing a knob
is how two datasets stop being comparable.

## Commands

```
scripts/factory.py add-board PATH [--source fixture|golden|run] [--family NAME]
scripts/factory.py add-runs                                  # every routed candidate in `candidates`
scripts/factory.py windows (BOARD_ID | --all) [--wait]       # make factory-windows
make dataset KIND=cut SHARDS=8 N=1000 SEED=1                 # synthetic cuts through the queue
make train-cutnet DATASET=<id> VERSION=v6 [REAL=pic_programmer] [MODEL=gbr]
scripts/factory.py boards
scripts/factory.py show GEOMETRY_HASH_PREFIX
```
