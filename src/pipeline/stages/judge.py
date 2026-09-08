"""Stage 6: score every candidate (reference judge, or JUDGE_URL), pick the best one."""

from __future__ import annotations

import json

import httpx

from pipeline import board, judge, models, storage
from pipeline.config import settings
from pipeline.stages import Ctx, common, stage


def _score(run_id: int, text: str, nl: models.Netlist, c: models.Constraints) -> models.JudgeResult:
    if not settings.judge_url:
        return judge.score(board.parse(text), nl, c)
    headers = {"Authorization": f"Bearer {settings.judge_token}"} if settings.judge_token else {}
    r = httpx.post(
        f"{settings.judge_url}/v1/score",
        json={
            "run_id": run_id,
            "netlist": nl.to_json(),
            "constraints": c.to_json(),
            "board_pcb": text,
        },
        headers=headers,
        timeout=120,
    )
    r.raise_for_status()
    d = r.json()
    return models.JudgeResult(
        d["score"], d["metrics"], [models.Violation(**v) for v in d["violations"]], d["judge"]
    )


@stage("judge")
def judge_stage(ctx: Ctx) -> None:
    nl = common.load_netlist(ctx)
    c = common.load_constraints(ctx)
    with ctx.conn.cursor() as cur:
        cur.execute(
            "select id, seed, board_key from candidates where run_id = %s order by seed",
            (ctx.run_id,),
        )
        rows = cur.fetchall()
    if not rows:
        raise RuntimeError("no candidates to judge")

    scored: list[tuple[int, int, str, models.JudgeResult]] = []
    with ctx.conn.cursor() as cur:
        for cid, seed, key in rows:
            text = storage.get(key).decode()
            res = _score(ctx.run_id, text, nl, c)
            scored.append((cid, seed, text, res))
            cur.execute(
                "update candidates set score = %s, metrics = %s where id = %s",
                (res.score, json.dumps(res.metrics), cid),
            )
        # The router reported unrouted connections per seed; a candidate with any is never
        # chosen over a fully routed one (the judge only sees nets with no copper at all).
        cur.execute(
            "select details from stages where run_id = %s and name = 'route' and status = 'done' "
            "order by id desc limit 1",
            (ctx.run_id,),
        )
        row = cur.fetchone()
        unrouted = {
            int(k): v["unrouted"] for k, v in (row[0] if row else {}).items() if k.isdigit()
        }
        routed = [r for r in scored if unrouted.get(r[1], 0) == 0] or scored
        best_id, best_seed, best_text, best = max(routed, key=lambda r: r[3].score)
        cur.execute(
            "update candidates set chosen = (id = %s) where run_id = %s", (best_id, ctx.run_id)
        )

    report = {
        "chosen_seed": best_seed,
        "candidates": [
            {"seed": seed, "score": res.score, "violations_count": len(res.violations)}
            for _, seed, _, res in scored
        ],
        "chosen": best.to_json(),
    }
    common.save_json(ctx, "report.json", report)
    ctx.provenance(best.judge["name"], best.judge["version"], storage.sha256(best_text.encode()))
    ctx.output_hash = storage.sha256(json.dumps(report).encode())
    ctx.details["scores"] = {str(seed): res.score for _, seed, _, res in scored}
    ctx.details["violations"] = {str(seed): len(res.violations) for _, seed, _, res in scored}
