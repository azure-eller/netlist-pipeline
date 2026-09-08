# Judge v2: a distilled learned judge

**What it is.** A `GradientBoostingRegressor` (scikit-learn defaults) that predicts the rule
judge's score from 19 board features (`pipeline.learned.FEATURES`: total/per-class track
length, via and segment and footprint counts, board area, min power-rail width, max and mean
decoupling distance, vias on high-speed nets, impedance error ratio min/mean, max diff-pair
mismatch, unrouted fraction, zone-net count). It is served behind the same `/v1/score` contract
as the rule judge by `JUDGE_VERSION=v2 make judge`; `/v1/info` reports
`learned-gbr / v2 / ["score"]` plus the artifact's sha256 and load time.

**The caveat, first.** This is a distilled copy of the rule judge v0.1.0, trained on the rule
judge's own labels and on features derived from its own metrics. It exists to exercise
artifact versioning, loading, gating and serving. It is not better physics, it emits no
violations, and it should never be preferred over the rules for a real decision.

**Corpus.** Three real boards, each plus 2000 in-memory mutations of the parsed `Board`
(1 to 4 of: move a random capacitor 0-40 mm, scale a random power rail's track widths by
0.3-2x, add 1-4 vias to a random high-speed net, drop every segment of a random net):

| board | source | rule score of the original |
|---|---|---|
| pic_programmer, human layout | `tests/fixtures/pic_programmer/pic_programmer.kicad_pcb` | -6.480 |
| pic_programmer, generated | chosen candidate of local run 34 | -1.198 |
| rpi_hat, generated | chosen candidate of local run 27 | -3.430 |

Netlists come from `runs/<id>/netlist.json` and constraints from the `constraints` table of
those runs. 6003 samples, 80/20 split with seed 0.

**Numbers (held-out 1201 samples).** R² 0.9896, MAE 0.3455 score points. Training takes
about 3 s end to end.

**Command.** `make train-judge` (= `scripts/train_judge.py`, needs the local Postgres and
MinIO with runs 27 and 34 present). It writes `models/judge-v2.joblib` (git-ignored) and
uploads it to S3 key `models/judge/v2.joblib`. The artifact dict carries `name`, `version`,
`model`, `feature_names`, `trained_at`, `corpus_size`, `r2`, `mae`, and the caveat as `note`.
Artifact sha256 of this training: `56b460fcd3bafb624ef94573dae4febd8fa964e99467c0e16e0d273d212c9f14`.

## Gate results and iterations (2026-09-08)

Every line below is in `golden.jsonl`. Tolerance is max(0.5, 10%) of the expected score;
ordering constraints (broken board scores below its parent) held for every version.

| version | capacitor moves in corpus | R² held out | pic_human | pic_generated | rpi_generated | pic_cap_far (exp -16.12) | rpi_thin | gate |
|---|---|---|---|---|---|---|---|---|
| rules 0.1.0 | reference | | -6.48 | -1.20 | -3.43 | -16.12 | -4.99 | approved |
| learned-gbr v2 | 0-40 mm | 0.990 | -6.58 | -1.32 | -3.03 | -10.31 | -4.95 | refused |
| learned-gbr v3 | 0-100 mm | 0.987 | -6.67 | -1.32 | -3.00 | -14.35 | -4.77 | refused |
| learned-gbr v4 | 0-150 mm | 0.985 | -6.69 | -1.27 | -3.01 | -14.10 | -4.90 | refused |

Reading: all three learned versions track the real boards within tolerance and fail the same
deliberately broken case, a capacitor 93 mm from its IC pin, whose penalty saturates in the rule
judge (relative excess clipped at 5). A regressor trained on mostly unsaturated examples
underestimates the plateau even when the corpus reaches it. Three iterations are enough to say
that widening the move range does not fix it; the next lever is the feature set (a clipped
distance feature) or a different loss, and that is a research decision, recorded here for
whoever makes it. No learned judge is approved.

The gate in the worker was exercised both ways on the same day:

- run 58, `JUDGE_URL` at v4: judge stage failed with `judge learned-gbr v4 is not in
  golden/approved.json; run scripts/golden.py --judge-url ... --approve`.
- run 59, `JUDGE_URL` at the reference judge served by `judge_api.py` with
  `JUDGE_VERSION=rules`: run passed, judge stage recorded `rules 0.1.0` via the remote path
  with the same score as in-process (-6.48).
