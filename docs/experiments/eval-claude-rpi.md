# Eval

API `http://localhost:8000`, seeds per generate run: 3, placer: claude. Human layouts are judged by the same judge and verified by the same checks as the generated ones.

placer=claude, Fable 5.1, effort medium, 20 min cap, commit 6a394ee

| fixture / config | placer | status | verified | DRC errors | unrouted | score | proxy cost | track mm | vias | violations | s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rpi_hat / claude x3 (generate) | claude | done | True | 0 | 0 | -3.43 | 666 -> 680, 675 -> 679, 666 -> 675 | 164 | 4 | 2 | 202 |
