### cutnet v6a (real families none): 82288 parameters, 400 epochs, seed 0

Artifact `models/judge/v6a-863b4755f8ab.joblib`, sha256 `863b4755f8abdd31c563a062ee3401ebe781993801c7b95b2a28edb32b2cc369`. Trained 2026-09-09 16:56 on dataset 45 (6400 training cuts).

| set | n | z0 MAPE | z0 p95 | coupling MAE | log C MAE |
|---|---|---|---|---|---|
| synthetic held-out | 1600 | 11.86% | 44.73% | 0.1021 | 0.1507 |
| fresh synthetic | 200 | 13.99% | 44.84% | 0.1070 | 0.1619 |
| real pic_programmer (never seen) | 2680 | 27.92% | 49.68% | 0.2141 | 0.3036 |
| real rpi_hat (never seen) | 1379 | 35.46% | 54.10% | 0.1711 | 0.4085 |

81 s. Command: `scripts/train_cutnet.py --dataset 45 --version v6a --real none --model cutnet`.

