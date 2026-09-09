# Running and deploying

## Locally

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
positions, footprints for symbols that have none. The format is in [SPEC.md](../SPEC.md).

Fixtures are KiCad's own template and demo, unmodified, plus one `constraints.json` for the
template's unassigned footprints.

## Hosted

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
