# Eval

API `http://localhost:8000`, seeds per generate run: 3. Human layouts are judged by the same judge
and verified by the same checks as the generated ones.

| fixture / config | status | verified | DRC errors | unrouted | score | track mm | vias | violations | s |
|---|---|---|---|---|---|---|---|---|---|
| pic_programmer / human layout (judge) | done | True | 0 | 0 | -6.480213621029586 | 1745.7030331706992 | 6 | 3 | 6 |
| pic_programmer / search x3 (generate) | failed |  |  |  |  |  |  |  | 3 |
| rpi_hat / search x3 (generate) | failed |  |  |  |  |  |  |  | 3 |
