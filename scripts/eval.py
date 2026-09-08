"""Run the fixtures through the pipeline and write docs/EVAL.md.

For each fixture: judge mode on the human layout (the oracle) and generate mode with N seeds
(search). Same judge, same verification, side by side. Usage: scripts/eval.py [api_url] [seeds]
"""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 1


def zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                z.write(p, p.relative_to(d))
    return buf.getvalue()


def run(client: httpx.Client, name: str, data: bytes, **params: Any) -> dict[str, Any]:
    r = client.post("/designs", files={"file": (name, data)}, params=params)
    r.raise_for_status()
    run_id = r.json()["run_id"]
    t0 = time.time()
    while True:
        j = client.get(f"/runs/{run_id}").json()
        if j["status"] in ("done", "failed", "failed_verification"):
            j["seconds"] = time.time() - t0
            return j  # type: ignore[no-any-return]
        time.sleep(3)


def row(label: str, j: dict[str, Any]) -> str:
    v = j.get("verification") or {}
    chosen = next((c for c in j["candidates"] if c["chosen"]), None) or {}
    m = chosen.get("metrics") or {}
    totals = m.get("totals", {})
    cells = [
        label,
        j["status"],
        v.get("passed", ""),
        (v.get("drc") or {}).get("errors", ""),
        v.get("unrouted", ""),
        chosen.get("score", ""),
        totals.get("track_length_mm", ""),
        totals.get("via_count", ""),
        totals.get("violations", ""),
        f"{j['seconds']:.0f}",
    ]
    return "| " + " | ".join(str(c) for c in cells) + " |"


def main() -> None:
    client = httpx.Client(base_url=API, timeout=120)
    lines = [
        "# Eval",
        "",
        f"API `{API}`, seeds per generate run: {SEEDS}. Human layouts are judged by the same judge",
        "and verified by the same checks as the generated ones.",
        "",
        "| fixture / config | status | verified | DRC errors | unrouted | score "
        "| track mm | vias | violations | s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for fx in ("pic_programmer", "rpi_hat"):
        d = FIXTURES / fx
        has_routed_board = fx == "pic_programmer"
        if has_routed_board:
            lines.append(row(f"{fx} / human layout (judge)", run(client, f"{fx}.zip", zip_dir(d))))
        lines.append(
            row(
                f"{fx} / search x{SEEDS} (generate)",
                run(client, f"{fx}.zip", zip_dir(d), mode="generate", seeds=SEEDS),
            )
        )
        print(lines[-1])
    out = ROOT / "docs" / "EVAL.md"
    out.write_text("\n".join(lines) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
