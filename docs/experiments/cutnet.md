# The cut model: a transformer over a slice's conductors, trained on the general solver

2026-09-09. FACTORY.md steps 2 and 6. Every number below is from `scripts/train_cutnet.py`
on dataset 45 (8,000 synthetic cuts, sampler `cut-0.1`, solver `fd2d-cut-0.1`) and the 4,063
real cuts with a reference across 41 boards of two families. Per-run detail is appended by
the script to `cutnet-<version>.md`.

## What was trained

`pipeline/cutnet.py`: each conductor of a cut is a token (log width, signed offset, log
|offset|, is-target, same-net, plus the slice's log h, log t, er, plane); a three-layer,
eight-head transformer (d = 128) whose attention scores carry a bias from every pair's edge
gap; a symmetric pair head on (h_i + h_j, |h_i - h_j|) for log(-C_ij) and a diagonal head
for log C_ii, both for the dielectric and for air. Loss: MSE on standardised log|C| plus a
conservation penalty relu(sum_j |C_ij| - C_ii) / C_ii on the de-standardised matrices.
436,028 parameters, 800 epochs, Adam 1e-3 with cosine decay, batch 256, about a minute on
the RTX 5070. z0 and per-neighbour coupling come from `fields.derive`, the solver's own
formula, so the model is scored on quantities it never fits directly.

Baseline: gradient boosting (600 trees, depth 4) on ten hand features of the target and its
nearest neighbour each side, predicting log z0.

## Results

z0 error is MAPE over cuts (p95 in brackets); k is the mean absolute error of the coupling
to each neighbour. "Never seen" means no cut of that board family was in training.

| model | trained on | synthetic held-out (1,600) | fresh synthetic (200) | real pic_programmer (2,680) | real rpi_hat (1,379) |
|---|---|---|---|---|---|
| gradient boosting | synthetic | 3.25 % (8.8) | 3.51 % (9.3) | 9.54 % (22.6) never seen | 9.85 % (23.1) never seen |
| gradient boosting | synthetic + pic real | 3.38 % (8.7) | 3.80 % (10.7) | 1.69 % (4.7) trained on | 3.51 % (9.6) never seen |
| cutnet v7 | synthetic | 0.76 % (2.2), k 0.008 | 0.81 % (2.2), k 0.008 | 10.81 % (37.8), k 0.092 never seen | 16.01 % (38.8), k 0.103 never seen |
| **cutnet v8** | synthetic + pic real | **0.59 % (1.6), k 0.005** | **0.57 % (1.7), k 0.005** | 0.51 % (1.2), k 0.004 trained on | **1.77 % (4.5), k 0.012 never seen** |

Three things the table says:

1. **The network beats the trees on the physics it was trained on.** 0.6 % against 3.3 % on
   held-out synthetic slices, and the coupling to each neighbour, which the trees cannot
   predict at all, comes out within 0.005.
2. **Synthetic slices alone do not transfer to real boards.** v7 is worse than the baseline
   on real cuts (10.8 % and 16 %). Real cuts have up to six neighbours at every width and
   almost never a plane (46 of 5,206); `cut-0.1` draws at most four, a plane half the time.
   The sampler's ranges were chosen from stackups, not from the boards we now have windows
   of. Next sampler version: draw neighbour counts, widths and gaps from the real-cut
   histograms, plane probability from the boards.
3. **Real cuts of one family carry to the other.** Trained on pic_programmer's cuts as well,
   v8 reaches 1.8 % (p95 4.5 %) on rpi_hat, a board family it never saw, against 3.5 % for
   the baseline given the same data. The judge's impedance rule tolerates 10 %.

## Gate

The golden answer key was recaptured with the general solver on real cross-sections first
(`golden/README.md`): `rules 0.1.0` and `learned-fd v5` then fail the two impedance-live
cases (they assume a plane the HAT does not have). v8 passes all seven (`rpi_fast` -23.98
against -24.18, `rpi_fast_tuned` -4.15 against -4.16) and is approved: `judge_approvals` row
for `learned-cut v8`, artifact `6c0c931156ce…`, golden `b2033532db5b…`. A worker with
`JUDGE_URL` pointed at the v8 service regenerated the HAT with its I2C nets high-speed (run
86): the judge stage records `learned-cut v8`, `/ID_SCL` at 122 ohm from 15 real
cross-sections and `/ID_SDA` at 153 ohm from 11, both `reference: cut`, both flagged.

## Record of the broken runs

`cutnet-v6.md` holds v6a and v6, trained before the conservation penalty was fixed: it was
applied to standardised log values, so `exp` of them was not a capacitance and the penalty
fought the fit (validation loss plateaued at 0.016; 11.9 % on held-out). The `models` table
is immutable, so those rows stay; nothing was approved from them.

## Commands

```
make dataset KIND=cut SHARDS=8 N=1000 SEED=1            # dataset 45
scripts/factory.py add-runs && make factory-windows      # 41 boards, 508 windows, 5,206 cuts
make train-cutnet DATASET=45 VERSION=v7 MODEL=gbr        # baseline, writes cutnet-v7.md
make train-cutnet DATASET=45 VERSION=v7                  # synthetic only
make train-cutnet DATASET=45 VERSION=v8 REAL=pic_programmer
JUDGE_VERSION=v8 make judge; scripts/golden.py --judge-url http://localhost:8100 --approve
```
