# Ticket lane: the first card through OpenHands (2026-09-10)

The lane is described in the [agent-lane](https://github.com/azure-eller/agent-lane) repo. This
is the record of the first card that went through it against this repo.

## The card

Linear ERP-210, labels `agent` and `small`: "Make every script in scripts/ executable and add
a test". Model: DeepSeek V4 Pro through OpenRouter, the `cheap` profile.

## What happened

| run | trigger | outcome |
|---|---|---|
| 1 | card created; webhook arrived 9 s later | died at `mkdir`: the automation service sent its host workspace path into the container |
| 2 | label `small` added | cloned the repo, then could not reach the agent server: the entrypoint was given the host port (18100) instead of the in-container one (8000) |
| 3 | priority set | PR [#1](https://github.com/azure-eller/netlist-pipeline/pull/1) on branch `agent/ERP-210`, 8 min 30 s, $0.157 |

Both failures were configuration (`AUTOMATION_WORKSPACE_BASE`, `AUTOMATION_SANDBOX_AGENT_SERVER_URL`),
recorded in agent-lane's SETUP.md. No code was written to fix them.

## The pull request

Nine files: eight `chmod +x` and a nine-line `tests/test_scripts.py`. Nothing outside the
card. The body starts with `Verdict: DONE`, then what was verified: lint passes, the new test
passes, and it names three mypy errors and ten test collection errors as pre-existing and
unrelated, which they are.

## What it could not do

- Comment on the card and add `in-review`: no Linear API key on the agent server. Until one
  is stored, the card keeps re-firing on every edit (that is how runs 2 and 3 were triggered).
- Run the whole suite: the sandbox venv is built with `pip install -e '.[dev]'` and does not
  see torch, scipy, sklearn or joblib, which live in the host's system site-packages. 26 tests
  ran, 10 modules failed to collect. Follow-up: build those into the product image or install
  them in the automation prompt.

## Follow-ups it surfaced

- `make type` fails on main: `src/pipeline/cutnet.py` (2x cannot subclass `Module`) and
  `src/pipeline/placer_claude.py` (unused `type: ignore`). Clean locally, where torch is installed; in
  the sandbox torch is missing, the import resolves to `Any`, and strict mypy refuses to
  subclass it. Same code, different environment, so the finding is about the sandbox.
