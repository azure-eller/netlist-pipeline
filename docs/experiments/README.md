# Experiments

Results live here, not in memory. One row per experiment; the result file is the record.

| experiment | date | command | result file | verdict |
|---|---|---|---|---|
| golden set, rules judge 0.1.0 | 2026-09-07 | `make golden` then `scripts/golden.py --approve` | `golden.jsonl` (one line per run) | pass: 5/5 cases, both ordering constraints hold; rules 0.1.0 approved in `golden/approved.json` |
| learned judge v2, v3, v4 vs the golden set | 2026-09-08 | `scripts/train_judge.py v3 100`; `JUDGE_VERSION=v3 make judge`; `scripts/golden.py --judge-url http://localhost:8100` | `judge-v2.md`, `golden.jsonl` | all three refused: within tolerance on real boards, underestimate the saturated far-capacitor case (-10.3 / -14.4 / -14.1 vs -16.1); no learned judge approved |
| worker gate, both directions | 2026-09-08 | worker with `JUDGE_URL=http://localhost:8100 JUDGE_VERSION=v4`, then `=rules` | `judge-v2.md` (runs 58, 59) | v4 refused at the judge stage; remote rules judge accepted with the in-process score |
| Claude placer, Fable 5.1 effort high, 3 seeds | 2026-09-08 | `scripts/eval.py http://localhost:8000 3 --placer claude --out docs/experiments/eval-claude.md` | `eval-claude.md`, `claude-placer.md`, `claude-placer/*.json` | see claude-placer.md |
