# Eval

API `http://localhost:8000`, seeds per generate run: 3. Human layouts are judged by the same judge
and verified by the same checks as the generated ones.

| fixture / config | status | verified | DRC errors | unrouted | score | track mm | vias | violations | s |
|---|---|---|---|---|---|---|---|---|---|
| pic_programmer / human layout (judge) | done | True | 0 | 0 | -6.48 | 1746 | 6 | 3 | 9 |
| pic_programmer / search x3 (generate) | done | True | 0 | 0 | -1.20 | 3023 | 8 | 2 | 244 |
| rpi_hat / search x3 (generate) | done | True | 0 | 0 | -3.43 | 150 | 2 | 2 | 24 |

## Reading it

- Every row verifies: DRC clean (silkscreen excluded), board connectivity equals the
  schematic, nothing unrouted, pads inside the outline. Generate mode on the 63-part board
  takes about four minutes for three seeds routed in parallel; two of three seeds route fully
  and the judge chooses among those.
- The rule judge scores the generated pic_programmer layout better than the human one
  (-1.20 vs -6.48) while the generated board uses 73% more copper (3023 mm vs 1746 mm). The
  judge weights decoupling distance, current capacity and impedance; it does not charge for
  wire length, via count beyond high-speed nets, or anything a field solver would see. That
  gap is what a learned physics judge is for, and this table is how it would be measured.
- Human layout violations: three decoupling capacitors beyond 10 mm from their IC pin.
  Generated: two, on rails the client's constraints.json marked as carrying 1 A.
