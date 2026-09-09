### cutnet v8 (real families pic_programmer): 436028 parameters, 800 epochs, seed 0

Artifact `models/judge/v8-6c0c931156ce.joblib`, sha256 `6c0c931156ceaa9462ad17df9a45b47eba812b4425cc6eb90b28bbfe0c41c2dc`. Trained 2026-09-09 17:05 on dataset 45 (9080 training cuts).

| set | n | z0 MAPE | z0 p95 | coupling MAE | log C MAE |
|---|---|---|---|---|---|
| synthetic held-out | 1600 | 0.59% | 1.64% | 0.0049 | 0.0211 |
| fresh synthetic | 200 | 0.57% | 1.68% | 0.0046 | 0.0216 |
| real pic_programmer (trained on) | 2680 | 0.51% | 1.18% | 0.0038 | 0.0047 |
| real rpi_hat (never seen) | 1379 | 1.77% | 4.55% | 0.0120 | 0.0991 |

171 s. Command: `scripts/train_cutnet.py --dataset 45 --version v8 --real pic_programmer --model cutnet`.

