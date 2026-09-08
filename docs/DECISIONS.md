# Decisions

One paragraph each. The question is always "what did we not build, and why."

**Postgres as the job queue, not Redis, SQS, or Celery.** One `jobs` table claimed with
`FOR UPDATE SKIP LOCKED`. The queue lives in the same transaction as the data it describes, so
a stage cannot finish without its results being recorded, and there is one fewer service to
run, monitor, and pay for. It scales past what two people will produce. Replace it when queue
throughput, not stage runtime, is the bottleneck.

**kicad-cli is the only source of connectivity.** We never derive nets from the schematic
drawing. Resolving wires, labels, buses, and hierarchical sheets is KiCad's job and it is right;
anything we wrote would be wrong in ways that surface as mis-wired boards.

**One Docker image for API and worker.** The API carries KiCad it never uses. Two images would
be smaller and slower to maintain. Same code, same versions, two start commands.

**Simulated annealing for placement.** No open placement model exists that is worth pulling in.
Annealing is a hundred lines, deterministic per seed, easy to add constraints to, and was the
standard for decades. The judge is not in the inner loop yet because it is not fast enough to
call thousands of times; the cost is a proxy (wire length, decoupling distance, overlap). When
a fast judge exists, it moves inside.

**Freerouting for routing.** Open source, twenty years old, headless, ships with its own JRE.
Routing is a solved-enough problem; the interesting parts are placement and judgment.

**Rule-based physics as the judge, behind an HTTP contract.** Closed-form impedance, current
capacity, crosstalk proxies, and decoupling distance are what engineers run as a first pass
today. It is simplified physics, not invented numbers, and it is the shape a learned model
replaces: same inputs, same outputs, a different function. The contract is one endpoint so the
swap is an environment variable.

**No language model in the pipeline.** Nothing in the loop needs one, and a model that does
not understand circuit physics has no business judging boards. Claude built the repo. Where a
model would earn a place later: reading datasheets into constraints, and explaining a failed
verification to an engineer.

**ERC is recorded, not a gate.** KiCad's own templates fail ERC on missing power flags. The
gates are DRC with schematic parity and netlist equivalence, which check the thing that ships.

**S3 keyed by content hash.** Same bytes, same key, stored once. Postgres holds the key.

**Render for the hosted parts, a local Docker Compose that is structurally identical.** What is
tested locally is what runs. Render has no object storage, so files live in S3; the bucket and
a least-privilege IAM user come from one CloudFormation template whose outputs include the
secret, acceptable for a demo account and not for production.

**A golden set with a gate, not a benchmark.** Fifty boards a slow oracle already judged is the
ideal; we have five boards the rule judge judged, two of them deliberately broken. The gate
checks agreement within tolerance and, more importantly, ordering: the broken board must score
worse than its parent. Expected answers from the rule judge catch regressions and disagreement,
not truth. When an oracle exists (openEMS, bench measurements), the same script takes its
answers. A judge version gets into the worker only after `--approve` records it.

**The learned judge is a distillation, and says so.** A gradient-boosted regressor trained on
the rule judge's own scores exists to make versioned serving, artifact loading, `/v1/info`,
and the gate real. It is not better physics and every artifact carries that note. Building the
plumbing with a toy is cheaper than waiting for the model, and the plumbing is the job.

**Claude as a placer is an experiment with a row in the table, not a feature.** Nobody has
shown a language model beats search at placement on real boards. The pipeline can measure it:
same router, same judge, same verification, a record on disk per proposal. Whatever the table
says is what gets said on the call.
