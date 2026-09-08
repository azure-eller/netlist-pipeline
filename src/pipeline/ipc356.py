"""Parser for IPC-D-356 as written by `kicad-cli pcb export ipcd356`.

KiCad writes net names upper-cased and truncated to their last 14 characters; `{slash}` and
friends are KiCad's own net-name escapes (upper-cased along with the rest)."""

from __future__ import annotations

from collections import defaultdict

# KiCad string_utils.cpp UnescapeString tokens, upper-cased as the IPC exporter emits them.
ESCAPES = {
    "{DBLQUOTE}": '"',
    "{QUOTE}": "'",
    "{LT}": "<",
    "{GT}": ">",
    "{BACKSLASH}": "\\",
    "{SLASH}": "/",
    "{BAR}": "|",
    "{COMMA}": ",",
    "{COLON}": ":",
    "{SPACE}": " ",
    "{DOLLAR}": "$",
    "{TAB}": "\t",
    "{RETURN}": "\n",
    "{BRACE}": "{",
}


def unescape(name: str) -> str:
    for token, ch in ESCAPES.items():
        name = name.replace(token, ch)
    return name


def parse(text: str) -> dict[str, frozenset[str]]:
    """net name -> {"REF.PIN"}. Vias and unassigned holes (no pin) and N/C are skipped."""
    nets: dict[str, set[str]] = defaultdict(set)
    for line in text.splitlines():
        if line[:3] not in ("317", "327"):
            continue
        net = line[3:17].rstrip()
        if net == "N/C" or line[26:27] != "-":
            continue
        nets[unescape(net)].add(f"{line[20:26].rstrip()}.{line[27:31].rstrip()}")
    return {k: frozenset(v) for k, v in nets.items()}
