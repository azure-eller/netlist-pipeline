### cutnet v7 (real families none): 436028 parameters, 800 epochs, seed 0

Artifact `models/judge/v7-a71fa931ecf4.joblib`, sha256 `a71fa931ecf49d57a36242c0ec983ca5b6986298b7197f2670bb6951b8392277`. Trained 2026-09-09 17:02 on dataset 45 (6400 training cuts).

| set | n | z0 MAPE | z0 p95 | coupling MAE | log C MAE |
|---|---|---|---|---|---|
| synthetic held-out | 1600 | 0.76% | 2.17% | 0.0078 | 0.0248 |
| fresh synthetic | 200 | 0.81% | 2.23% | 0.0081 | 0.0265 |
| real pic_programmer (never seen) | 2680 | 10.81% | 37.82% | 0.0918 | 0.1808 |
| real rpi_hat (never seen) | 1379 | 16.01% | 38.81% | 0.1031 | 0.4140 |

134 s. Command: `scripts/train_cutnet.py --dataset 45 --version v7 --real none --model cutnet`.

