### baseline: gradient boosting on hand features (real families none)

| set | n | z0 MAPE | z0 p95 |
|---|---|---|---|
| synthetic held-out | 1600 | 3.25% | 8.84% |
| fresh synthetic | 200 | 3.51% | 9.29% |
| real pic_programmer (never seen) | 2680 | 9.54% | 22.60% |
| real rpi_hat (never seen) | 1383 | 9.85% | 23.07% |

50 s. Command: `scripts/train_cutnet.py --dataset 45 --version v6 --real none --model gbr`.

### baseline: gradient boosting on hand features (real families pic_programmer)

| set | n | z0 MAPE | z0 p95 |
|---|---|---|---|
| synthetic held-out | 1600 | 3.38% | 8.74% |
| fresh synthetic | 200 | 3.80% | 10.71% |
| real pic_programmer (trained on) | 2680 | 1.69% | 4.65% |
| real rpi_hat (never seen) | 1383 | 3.51% | 9.63% |

51 s. Command: `scripts/train_cutnet.py --dataset 45 --version v6 --real pic_programmer --model gbr`.

### cutnet v6 (real families pic_programmer): 82288 parameters, 400 epochs, seed 0

Artifact `models/judge/v6-66f7ed1bbd5a.joblib`, sha256 `66f7ed1bbd5a914118b8f59f4f034764d3754c5bdb561452c656b066482a70d3`. Trained 2026-09-09 16:58 on dataset 45 (9080 training cuts).

| set | n | z0 MAPE | z0 p95 | coupling MAE | log C MAE |
|---|---|---|---|---|---|
| synthetic held-out | 1600 | 9.69% | 38.26% | 0.0896 | 0.1243 |
| fresh synthetic | 200 | 11.32% | 38.89% | 0.0939 | 0.1341 |
| real pic_programmer (trained on) | 2680 | 18.77% | 40.91% | 0.1437 | 0.2061 |
| real rpi_hat (never seen) | 1379 | 25.86% | 44.36% | 0.1304 | 0.2401 |

105 s. Command: `scripts/train_cutnet.py --dataset 45 --version v6 --real pic_programmer --model cutnet`.

