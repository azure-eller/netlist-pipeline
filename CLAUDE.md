# netlist-pipeline

Backend that turns a KiCad schematic into a physics-judged, verified, fabrication-ready board
through recorded, replayable stages. SPEC.md is the contract: read it before changing behavior
and change it in the same change when behavior changes.

## Commands

- `make up` starts Postgres and MinIO; `make migrate` applies `migrations/*.sql`.
- `make api` and `make worker` run the two processes. `.env` holds local settings.
- `make check` runs ruff, mypy, unit tests, and the end-to-end suite. Nothing is done until it
  passes. Report its output, not a claim.

## Rules

- `kicad-cli` is the only source of connectivity. Never derive nets from the drawing.
- New schema goes in a new `migrations/NNN_name.sql`. Never edit an applied migration.
- Work that can exceed a second goes through the `jobs` table, never an API handler.
- Every stage records provenance via `ctx.provenance(tool, version, input_hash)` and
  `ctx.output_hash`.
- Ask before anything destructive to services or data: `compose down -v`, dropping tables,
  deleting objects, resetting the jobs table, anything on Render or AWS.
- A pre-existing bug the task doesn't mention is a follow-up, not a fix in the same change.
  Where the task is ambiguous, implement the reading the spec most directly supports and state
  the assumption. No abstractions, config, or error handling for scenarios that cannot happen.
- Factory work (`windows.py`, `data.py`, `scripts/factory.py`, harvest, tiers, models on
  windows): read `docs/FACTORY.md` first and update its status lines in the same change.
- Commit tests only for stated behaviors, one focused test each, next to the existing ones.

## Layout

`src/pipeline/` is the package: `api.py` (FastAPI), `worker.py` (job loop), `stages/` (one
function per stage, registered with `@stage`), `models.py` (shared value types), `sexpr.py`
(KiCad s-expression reader), `kicad.py` (kicad-cli wrapper), `pcb.py` (pcbnew helpers),
`judge.py` (rule-based physics), `placer.py`, `verify.py`. `tests/fixtures/` holds real KiCad
projects used as oracles. Local tools: kicad-cli 10 and `pcbnew` on the system Python (the venv
uses `--system-site-packages`), Freerouting at `$FREEROUTING_BIN`.
