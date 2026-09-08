"""kicad-cli wrapper. Every call records nothing itself; stages record provenance."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

from pipeline import log
from pipeline.config import settings


class KicadError(RuntimeError):
    pass


def run(
    args: list[str | Path], cwd: Path | None = None, timeout: int = 600
) -> subprocess.CompletedProcess[str]:
    argv = [str(a.resolve()) if isinstance(a, Path) else a for a in args]
    log.get("kicad").info("kicad_cli", args=argv[:4])
    env = dict(os.environ)
    env.setdefault("HOME", tempfile.gettempdir())  # kicad-cli wants a writable config dir
    p = subprocess.run(
        [settings.kicad_cli, *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if p.returncode != 0:
        raise KicadError(
            f"kicad-cli {' '.join(argv)} failed ({p.returncode}): {p.stderr.strip()[-2000:]}"
        )
    return p


@cache
def version() -> str:
    return run(["version"]).stdout.strip()


def erc(sch: Path) -> dict[str, Any]:
    out = sch.parent / "erc.json"
    run(
        ["sch", "erc", "--format", "json", "--severity-all", "-o", out, sch],
        cwd=sch.parent,
    )
    return json.loads(out.read_text())  # type: ignore[no-any-return]


def export_netlist(sch: Path) -> str:
    out = sch.parent / "netlist.net"
    run(
        ["sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch],
        cwd=sch.parent,
    )
    return out.read_text()


def drc(pcb: Path, parity: bool = True) -> dict[str, Any]:
    out = pcb.parent / "drc.json"
    args = ["pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors"]
    if parity:
        args.append("--schematic-parity")
    run([*args, "-o", out, pcb], cwd=pcb.parent)
    return json.loads(out.read_text())  # type: ignore[no-any-return]


def export_ipcd356(pcb: Path) -> str:
    out = pcb.parent / "board.d356"
    run(["pcb", "export", "ipcd356", "-o", out, pcb], cwd=pcb.parent)
    return out.read_text()


def export_gerbers(pcb: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["pcb", "export", "gerbers", "-o", str(out_dir.resolve()) + "/", pcb], cwd=pcb.parent)
    return sorted(p for p in out_dir.iterdir() if p.is_file())


def export_drill(pcb: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(
        ["pcb", "export", "drill", "--format", "excellon", "-o", str(out_dir.resolve()) + "/", pcb],
        cwd=pcb.parent,
    )
    return sorted(p for p in out_dir.iterdir() if p.is_file())


def export_pos(pcb: Path, out: Path) -> Path:
    run(
        ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "-o", out, pcb],
        cwd=pcb.parent,
    )
    return out


def export_stats(pcb: Path) -> dict[str, Any]:
    out = pcb.parent / "stats.json"
    run(["pcb", "export", "stats", "--format", "json", "-o", out, pcb], cwd=pcb.parent)
    return json.loads(out.read_text())  # type: ignore[no-any-return]


def render(pcb: Path, out: Path, side: str = "top", width: int = 1600, height: int = 1200) -> Path:
    run(
        [
            "pcb",
            "render",
            "--side",
            side,
            "--width",
            str(width),
            "--height",
            str(height),
            "-o",
            out,
            pcb,
        ],
        cwd=pcb.parent,
        timeout=900,
    )
    return out
