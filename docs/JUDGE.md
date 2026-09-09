# The judge

The physics judge is a slot. `pipeline/judge.py` is the reference implementation: closed-form
physics (IPC-2141 impedance, IPC-2221 current capacity, decoupling distance, crosstalk and via
proxies). A learned model replaces it behind one HTTP contract and nothing else changes.

## Contract

```
POST $JUDGE_URL/v1/score   Authorization: Bearer <token>
{"run_id": 1, "netlist": {...}, "constraints": {...}, "board_pcb": "<.kicad_pcb text>"}
-> {"score": -3.43, "metrics": {"nets": {...}, "rails": {...}, "totals": {...}},
    "violations": [{"rule": "current", "net": "+5V", "measured": 0.74, "threshold": 1.0, ...}],
    "judge": {"name": "rules", "version": "0.1.0"}}
```

The worker calls the reference in-process unless `JUDGE_URL` is set. `pipeline/judge_api.py`
serves the same function behind the contract, or a versioned model artifact loaded from S3
(`JUDGE_VERSION=v2 make judge`; `GET /v1/info` reports name, version, artifact hash). Score is
0 for a board with no violations and decreases with each violation's weighted excess. The
full contract is in [SPEC.md](../SPEC.md).

## Approval gate

A judge version reaches the worker only through the golden set: `make golden` runs any judge
against five boards with expected scorecards, two of them deliberately broken, and
`scripts/golden.py --approve` records a passing version. The worker refuses anything else.
Three distilled learned judges were trained and all three were refused, correctly, on the
broken-capacitor case; the record is in [experiments](experiments/README.md).

## Physics providers

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
in [experiments/surrogate-v5.md](experiments/surrogate-v5.md) and [DATA.md](DATA.md).

## Where labels come from next

[FACTORY.md](FACTORY.md) is the roadmap for labelling real board geometry at volume: windows
(one net plus everything within 5 mm), cross-sections solved by the field solver, fidelity
tiers, edit pairs, frozen datasets, and a geometry-native model. Step 1 is built.

## What it finds on the fixtures

On the generated RPi HAT the judge reports two current violations: the client's
`constraints.json` said the rails carry 1 A and the router used 0.2 mm traces, which carry
0.74 A. On `pic_programmer`, KiCad's own demo with a human-routed board, it reports three
decoupling capacitors farther from their IC pins than the 10 mm default. Both are real.

## What the judge is not

It is simplified physics, not a field solver, and it does not charge for wire length.
[EVAL.md](EVAL.md) shows search beating the human layout on this judge while using 73% more
copper; that gap is what a replacement judge is measured against. No language model runs in
the pipeline. Claude built the repo; [DECISIONS.md](DECISIONS.md) says where a model would
earn a place later.
