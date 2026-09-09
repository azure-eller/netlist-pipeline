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

![Simulated annealing placing the pic_programmer](docs/media/anneal.gif)

*Stage 4 on KiCad's 63-part `pic_programmer` demo: parts start on the build_board grid and the
annealer trades weighted wire length against courtyard overlap while the temperature falls.
Orange nets are power, blue are high speed. 85,500 moves, 69 seconds, same final cost as run 36.*

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

![Provenance graph for run 79](docs/media/provenance_79.png)

*The same run as a hash graph, drawn from the `stages` and `artifacts` rows by
`scripts/provenance.py 79`. Each green arrow is one stage's `output_hash` equal to the next
stage's `input_hash`; judge, verify and export all hash the routed board. Artifacts hang under
the stage whose output they are.*

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
set; `pipeline/judge_api.py` serves the same function behind the contract, or a versioned
model artifact loaded from S3 (`JUDGE_VERSION=v2 make judge`; `GET /v1/info` reports name,
version, artifact hash). Score is 0 for a board with no violations and decreases with each
violation's weighted excess.

A judge version reaches the worker only through the golden set: `make golden` runs any judge
against five boards with expected scorecards, two of them deliberately broken, and
`scripts/golden.py --approve` records a passing version. The worker refuses anything else.
Three distilled learned judges were trained and all three were refused, correctly, on the
broken-capacitor case; the record is in [docs/experiments](docs/experiments/README.md).

## The physics pipeline: oracle, data, surrogate

The judge's rules are fixed; where its impedance numbers come from is a provider. Three exist:

| provider | what | speed | gate |
|---|---|---|---|
| `rules 0.1.0` | IPC-2141 closed form, the original reference | microseconds | passes |
| `oracle-fd fd2d-0.1` | our own 2D quasi-static field solver on the trace cross-section (`pipeline/fields.py`), within 1% of the Hammerstad closed form | 0.36 s per geometry | is the truth |
| `learned-fd v5` | gradient boosting trained on 5,000 oracle-solved geometries | microseconds | passes |

The data pipeline is the same queue that builds boards: `make dataset SHARDS=4 N=1250` samples
cross-sections from ranges seen on real boards, solves them in shard jobs, and writes hashed
JSONL shards plus a manifest naming sampler version, solver version and seed. `make
train-surrogate DATASET=17 VERSION=v5` trains, scores held-out and fresh oracle-solved
geometries, uploads a versioned artifact and records it in the `models` table. The golden
set's expected answers come from the oracle; a surrogate is approved only if it agrees.

Surrogate v5 on 200 fresh geometries the oracle solved after training: 0.90% mean error on
single-trace impedance (p95 2.4%), 2.1% and 1.7% on odd and even mode impedance. Full record
in [docs/experiments/surrogate-v5.md](docs/experiments/surrogate-v5.md) and
[docs/DATA.md](docs/DATA.md).

## The data factory

Where a geometry model's labels would come from ([docs/FACTORY.md](docs/FACTORY.md) is the
roadmap with status lines). The unit is the window: one net plus everything within 5 mm, with
the stackup, re-origined and content-hashed. `scripts/factory.py add-board` registers a board,
`make factory-windows` cuts every board into windows through the queue, and each window's
perpendicular cross-sections (every conductor the cut crosses, whether a pour sits on the
adjacent layer) are labelled with the field solver. Step 1 labels the target conductor alone;
the general solver, real boards from GitHub, fidelity tiers, edit pairs, frozen datasets, and
the FNO are the next steps in that file.

## Deploy

`render.yaml` describes the hosted layout: `netlist-api` (web, free tier), `netlist-worker`
(background worker), `netlist-db` (Postgres). The bucket and a least-privilege IAM user come
from `infra/aws.yaml`:

```
aws cloudformation deploy --region us-west-2 --stack-name netlist-pipeline \
  --template-file infra/aws.yaml --capabilities CAPABILITY_NAMED_IAM
```

Live: https://netlist-api-ivqr.onrender.com/healthz (the web service sleeps when idle; the
first request after sleep takes about thirty seconds). The worker needs the 2 GB instance:
measured peak memory on the 63-part board is 434 MB for `kicad-cli pcb drc` alone, 316 MB for
the netlist export, plus the worker and its child interpreter, and the 512 MB starter instance
was out-of-memory killed on the first stage. Each job runs in a child interpreter; the KiCad
bindings are imported only by the stages that need them.

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
