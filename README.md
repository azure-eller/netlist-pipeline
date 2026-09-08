# netlist-pipeline

Schematic in, physics-judged, independently verified, fabrication-ready board out.

A KiCad schematic goes in. A worker extracts the netlist, derives constraints, builds a board,
places parts by simulated annealing, routes with Freerouting, scores the physics with a
rule-based judge, verifies the result with KiCad's own design-rule check and a netlist
equivalence test, and exports Gerbers. Every stage is a recorded job with tool version, input
hash, and output hash. Upload a board you already laid out and the same pipeline judges and
verifies it instead.

![RPi HAT generated from its schematic](docs/rpi_hat_generated.png)

*The KiCad RaspberryPi-HAT template, generated from the schematic alone: header pinned by the
client's constraints, EEPROM and passives placed by search, routed, DRC-clean, netlist verified
against the schematic. About forty seconds.*

The physics judge is a slot. The reference implementation is closed-form physics (IPC-2141
impedance, IPC-2221 current capacity, decoupling distance, crosstalk and via proxies). A learned
model replaces it behind one HTTP contract and nothing else changes. That slot is the point of
the repo: everything around a physics model, built so the model can be dropped in.

## Architecture

```mermaid
flowchart LR
    client([client]) -->|POST /designs| api[api<br/>FastAPI]
    api --> pg[(Postgres<br/>designs, runs, jobs,<br/>stages, nets, candidates,<br/>verifications, artifacts)]
    api --> s3[(S3<br/>uploads, boards,<br/>reports, gerbers)]
    worker[worker<br/>kicad-cli, pcbnew,<br/>Freerouting] -->|claim job<br/>SKIP LOCKED| pg
    worker --> s3
    client -->|GET /runs/id| api
```

Two containers from one image, a managed Postgres, and a bucket. The API never does work that
takes more than a second; it writes a job row and the worker picks it up. The two never talk to
each other. Locally, `docker compose` runs the same layout with MinIO standing in for S3.

```
 1  extract_netlist   kicad-cli ERC + netlist export -> components, nets, nodes
 2  constraints       project net classes + name patterns + constraints.json
 3  build_board       supplied board, or outline + library footprints wired to nets
 4  place             simulated annealing (generate mode only)
 5  route             Freerouting via Specctra DSN/SES (generate mode only)
 6  judge             physics score per candidate; best one chosen   <- the model slot
 7  verify            DRC with schematic parity, IPC-D-356 netlist == schematic, in bounds
 8  export            gerbers, drill, positions, stats, render, report
```

Any stage failure marks the run failed with the error text and stops. Verification failing
marks the run `failed_verification` and still keeps the artifacts for inspection. The judge
picks the best candidate; only verify decides whether the run passed.

## A run

```
$ scripts/demo.sh http://localhost:8000 tests/fixtures/rpi_hat generate
== upload upload.zip -> http://localhost:8000
{"design_id":19,"run_id":19,"sha256":"4d8f272d..."}
   running extract_netlist:done constraints:done build_board:done place:done route:running
   done    extract_netlist:done constraints:done build_board:done place:done route:done judge:done verify:done export:done
  extract_netlist  done  kicad-cli        10.0.5   {"erc": {"errors": 3, "warnings": 0, ...}, "nets": 35, "components": 6}
  constraints      done  pipeline         0.1.0    {"fixed": 1, "classes": {"power": 3, "default": 25, "high_speed": 7}, ...}
  build_board      done  pcbnew           10.0.5   {"nets": 35, "source": "template", "footprints": 10, "outline_mm": [65.1, 56.1]}
  place            done  pipeline-placer  0.1.0    {"1": 307.67}
  route            done  freerouting      2.4.1    {"1": {"seconds": 8.2, "unrouted": 0}, "passes": 20}
  judge            done  rules            0.1.0    {"scores": {"1": -3.43}, "violations": {"1": 2}}
  verify           done  kicad-cli        10.0.5   {"errors": 0, "passed": true, "unrouted": 0, "in_bounds": true, "netlist_match": true}
  export           done  kicad-cli        10.0.5
verification: {'passed': True, 'netlist_match': True, 'unrouted': 0, 'in_bounds': True}
artifacts: ['board.kicad_pcb', 'board.png', 'drill.zip', 'gerbers.zip', 'positions.csv', 'report.json', 'stats.json']
== physics report: score -3.43
   current +5V  +5V: 0.2 mm carries 0.74 A, needs 1.0 A
   current GND  GND: 0.2 mm carries 0.74 A, needs 1.0 A
```

The two findings are real: the client's `constraints.json` said the rails carry 1 A and the
router used 0.2 mm traces. Same board judged by hand-placed reference, `pic_programmer`,
KiCad's own demo with a human-routed board: verified, and the judge reports three decoupling
capacitors farther from their IC pins than the 10 mm default.

## Run it

```
make up && make migrate        # Postgres + MinIO in docker compose
make api                       # FastAPI on :8000 (docs at /docs)
make worker                    # the job loop; run more for more throughput
make demo                      # upload pic_programmer, poll, print the report
make check                     # ruff, mypy strict, unit tests, end-to-end suite
make eval                      # docs/EVAL.md: human layout vs search, per fixture
```

Needs KiCad 10 (`kicad-cli` and the `pcbnew` Python bindings; the venv is created with
`--system-site-packages`) and Freerouting (`FREEROUTING_BIN` in `.env`). The Dockerfile builds
on `kicad/kicad:10.0` and bundles Freerouting with its own JRE, so the container needs neither.

```
curl -F file=@project.zip 'http://localhost:8000/designs?mode=generate&seeds=3'
curl http://localhost:8000/runs/1
curl -L http://localhost:8000/runs/1/artifacts/gerbers.zip -o gerbers.zip
```

Upload a `.kicad_sch` alone, or a zip of the project directory. A zip containing a `.kicad_pcb`
runs in judge mode unless `?mode=generate`. A `constraints.json` in the zip supplies what the
files cannot: net classes, differential pairs, rail currents, impedance targets, fixed part
positions, footprints for symbols that have none. The format is in [SPEC.md](SPEC.md).

## The judge contract

```
POST $JUDGE_URL/v1/score   Authorization: Bearer <token>
{"run_id": 1, "netlist": {...}, "constraints": {...}, "board_pcb": "<.kicad_pcb text>"}
-> {"score": -3.43, "metrics": {"nets": {...}, "rails": {...}, "totals": {...}},
    "violations": [{"rule": "current", "net": "+5V", "measured": 0.74, "threshold": 1.0, ...}],
    "judge": {"name": "rules", "version": "0.1.0"}}
```

`pipeline/judge.py` is the reference. The worker calls it in-process unless `JUDGE_URL` is
set; `pipeline/judge_api.py` serves the same function behind the contract. Score is 0 for a
board with no violations and decreases with each violation's weighted excess.

## Deploy

`render.yaml` describes the hosted layout: `netlist-api` (web, free tier), `netlist-worker`
(background worker), `netlist-db` (Postgres). The bucket and a least-privilege IAM user come
from `infra/aws.yaml`:

```
aws cloudformation deploy --region us-west-2 --stack-name netlist-pipeline \
  --template-file infra/aws.yaml --capabilities CAPABILITY_NAMED_IAM
```

Live API: https://netlist-api-ivqr.onrender.com/healthz (free tier sleeps when idle; first
request after sleep takes about thirty seconds).

## Eval

`make eval` runs the fixtures both ways and writes [docs/EVAL.md](docs/EVAL.md):

| fixture / config | verified | score | track mm | seconds |
|---|---|---|---|---|
| pic_programmer, human layout (judge mode) | yes | -6.48 | 1746 | 9 |
| pic_programmer, search x3 (generate mode) | yes | -1.20 | 3023 | 244 |
| rpi_hat, search x3 (generate mode) | yes | -3.43 | 150 | 24 |

Search beats the human layout on the rule judge while using 73% more copper. The judge does
not charge for wire length; a physics model would. That gap is the point of the slot, and the
table is how a replacement judge gets measured.

## What is and is not here

- [SPEC.md](SPEC.md) is the contract: stages, constraints format, judge contract, invariants,
  definition of done.
- [docs/DECISIONS.md](docs/DECISIONS.md): what was not built and why.
- [docs/EVAL.md](docs/EVAL.md): search vs the human layout on the fixtures.
- No language model runs in the pipeline. Claude built the repo. See DECISIONS.md for where a
  model would earn a place later.
- The judge is simplified physics, not a field solver. It is the shape a learned model fills,
  with the slow oracle (openEMS) as the documented next step for sign-off.
- Fixtures are KiCad's own template and demo, unmodified, plus one `constraints.json` for the
  template's unassigned footprints.
