# Eval

API `http://localhost:8000`, seeds per generate run: 3. Human layouts are judged by the same judge
and verified by the same checks as the generated ones.

| fixture / config | status | verified | DRC errors | unrouted | score | track mm | vias | violations | s |
|---|---|---|---|---|---|---|---|---|---|
| pic_programmer / human layout (judge) | done | True | 0 | 0 | -6.480213621029586 | 1745.7030331706992 | 6 | 3 | 9 |
| pic_programmer / search x3 (generate) | failed |  |  |  |  |  |  |  | 1897 |
| rpi_hat / search x3 (generate) | done | True | 0 | 0 | -3.4299016474610946 | 141.73838482532167 | 2 | 2 | 42 |
