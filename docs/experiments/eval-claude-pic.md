# Eval

API `http://localhost:8000`, seeds per generate run: 3, placer: claude. Human layouts are judged by the same judge and verified by the same checks as the generated ones.

placer=claude, Fable 5.1, effort medium, 20 min cap, commit 5c67915

| fixture / config | placer | status | verified | DRC errors | unrouted | score | proxy cost | track mm | vias | violations | s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pic_programmer / human layout (judge) |  | done | True | 0 | 0 | -6.48 |  | 1746 | 6 | 3 | 9 |
| pic_programmer / claude x3 (generate) | claude | failed |  |  |  |  |  |  |  |  | 1282 |
