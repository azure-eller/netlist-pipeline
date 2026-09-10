"""Run the fixtures through the pipeline and write docs/EVAL.md.

For each fixture: judge mode on the human layout (the oracle) and generate mode with N seeds
using the chosen placer. Same judge, same verification, side by side.
Usage: scripts/eval.py [api_url] [seeds] [--placer search|claude] [--note TEXT]
"""

from __future__ import annotations

import argparse
import io
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


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


def proxy_cost(j: dict[str, Any]) -> str:
    """`search -> refined` per seed for claude runs (from the place stage details), else ''."""
    if j.get("placer") != "claude":
        return ""
    stage = next((s for s in j.get("stages", []) if s["name"] == "place"), None)
    details = (stage or {}).get("details") or {}
    return ", ".join(
        f"{d['search_cost']:.0f} -> {d['refined_cost']:.0f}"
        for _, d in sorted(details.items())
        if isinstance(d, dict)
    )


def row(label: str, j: dict[str, Any]) -> str:
    v = j.get("verification") or {}
    chosen = next((c for c in j["candidates"] if c["chosen"]), None) or {}
    m = chosen.get("metrics") or {}
    totals = m.get("totals", {})
    cells = [
        label,
        j.get("placer", "") if j["mode"] == "generate" else "",
        j["status"],
        v.get("passed", ""),
        (v.get("drc") or {}).get("errors", ""),
        v.get("unrouted", ""),
        round(chosen["score"], 2) if chosen.get("score") is not None else "",
        proxy_cost(j),
        round(totals["track_length_mm"]) if "track_length_mm" in totals else "",
        totals.get("via_count", ""),
        totals.get("violations", ""),
        f"{j['seconds']:.0f}",
    ]
    return "| " + " | ".join(str(c) for c in cells) + " |"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("api", nargs="?", default="http://localhost:8000")
    ap.add_argument("seeds", nargs="?", type=int, default=3)
    ap.add_argument("--placer", choices=("search", "claude"), default="search")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", default=str(ROOT / "docs" / "EVAL.md"))
    ap.add_argument("--fixtures", default="pic_programmer,rpi_hat")
    a = ap.parse_args()
    client = httpx.Client(base_url=a.api, timeout=120)
    lines = [
        "# Eval",
        "",
        f"API `{a.api}`, seeds per generate run: {a.seeds}, placer: {a.placer}. Human layouts "
        "are judged by the same judge and verified by the same checks as the generated ones.",
        *(["", a.note] if a.note else []),
        "",
        "| fixture / config | placer | status | verified | DRC errors | unrouted | score "
        "| proxy cost | track mm | vias | violations | s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for fx in a.fixtures.split(","):
        d = FIXTURES / fx
        has_routed_board = fx == "pic_programmer"
        if has_routed_board:
            lines.append(row(f"{fx} / human layout (judge)", run(client, f"{fx}.zip", zip_dir(d))))
        lines.append(
            row(
                f"{fx} / {a.placer} x{a.seeds} (generate)",
                run(
                    client,
                    f"{fx}.zip",
                    zip_dir(d),
                    mode="generate",
                    seeds=a.seeds,
                    placer=a.placer,
                ),
            )
        )
        print(lines[-1])
    out = Path(a.out)
    out.write_text("\n".join(lines) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
