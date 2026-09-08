# Surrogate v5: learned physics from the field-solver oracle

Trained 2026-09-08T07:03:42+00:00 on dataset 17 (5000 samples; sampler cross-section-0.1, solver fd2d-0.1). Gradient boosting, 600 trees, depth 4. Artifact `models/judge/v5.joblib`, sha256 `008b9bb862acb130b07afc561869bdb1848f118b30d4b6f804b79ad2b377541f`.

| model | train | test | held-out MAPE |
|---|---|---|---|
| single (z0) | 1980 | 495 | 0.91% |
| pair (z_odd, z_even) | 2020 | 505 | 1.91% / 1.71% |

Fresh geometries solved by the oracle after training (200 draws, unseen):

| target | n | MAPE | p95 |
|---|---|---|---|
| z0 | 103 | 0.90% | 2.40% |
| z_odd | 97 | 2.06% | 4.19% |
| z_even | 97 | 1.66% | 3.64% |

Training plus fresh evaluation took 81 s. Command: `scripts/train_surrogate.py --dataset 17 --version v5`.
