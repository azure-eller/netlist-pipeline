# Experiments

Results live here, not in memory. One row per experiment; the result file is the record.

| experiment | date | command | result file | verdict |
|---|---|---|---|---|
| golden set, rules judge 0.1.0 | 2026-09-07 | `make golden` then `scripts/golden.py --approve` | `golden.jsonl` (one line per run) | pass: 5/5 cases, both ordering constraints hold; rules 0.1.0 approved in `golden/approved.json` |
