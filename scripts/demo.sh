#!/usr/bin/env bash
# Upload a fixture, poll the run, print the report, download the Gerbers.
# usage: scripts/demo.sh [api_url] [fixture_dir_or_sch] [mode]
set -euo pipefail
API=${1:-http://localhost:8000}
FIX=${2:-tests/fixtures/pic_programmer}
MODE=${3:-}
TMP=$(mktemp -d)
if [ -d "$FIX" ]; then (cd "$FIX" && zip -qr "$TMP/upload.zip" .); UP="$TMP/upload.zip"; else UP="$FIX"; fi
Q=""; [ -n "$MODE" ] && Q="?mode=$MODE"
echo "== upload $(basename "$UP") -> $API"
RESP=$(curl -sf -F "file=@$UP" "$API/designs$Q"); echo "$RESP"
RUN=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")
LAST=""
while :; do
  J=$(curl -sf "$API/runs/$RUN")
  STATUS=$(echo "$J" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], ' '.join(f\"{s['name']}:{s['status']}\" for s in d['stages']))")
  [ "$STATUS" != "$LAST" ] && echo "   $STATUS" && LAST="$STATUS"
  case "$STATUS" in done*|failed*) break;; esac
  sleep 3
done
echo "== run $RUN"
echo "$J" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('status:', d['status'], d.get('error') or '')
for s in d['stages']: print(f\"  {s['name']:16} {s['status']:7} {s['tool'] or '':12} {s['tool_version'] or '':10} {json.dumps(s['details'])[:120]}\")
v=d.get('verification'); print('verification:', v and {k: v[k] for k in ('passed','netlist_match','unrouted','in_bounds')}, v and v['drc'].get('errors'))
for c in d['candidates']: print('candidate', c['seed'], 'score', c['score'], 'chosen' if c['chosen'] else '')
print('artifacts:', [a['name'] for a in d['artifacts']])
"
for A in report.json gerbers.zip; do
  if curl -sfL -o "$TMP/$A" "$API/runs/$RUN/artifacts/$A"; then echo "== downloaded $TMP/$A ($(stat -c %s "$TMP/$A") bytes)"; fi
done
[ -f "$TMP/report.json" ] && python3 -c "
import json; r=json.load(open('$TMP/report.json')); c=r['chosen']
print('== physics report: score', c['score']); [print('  ', v['rule'], v.get('net') or v.get('ref') or '', v['message']) for v in c['violations'][:15]]"
