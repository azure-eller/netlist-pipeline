@AGENTS.md

Claude-only notes: the local tools are kicad-cli 10 and `pcbnew` on the system Python (the
venv uses `--system-site-packages`), Freerouting at `$FREEROUTING_BIN`. Factory work
(`windows.py`, `data.py`, `scripts/factory.py`, harvest, tiers, models on windows): read
`docs/FACTORY.md` first and update its status lines in the same change. The agent lane
(Linear card to PR through OpenHands) lives in the sibling repo `agent-lane`; this repo only
provides `AGENTS.md` and the sandbox image (`make sandbox-image`).
