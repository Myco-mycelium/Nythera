#!/usr/bin/env bash
#
# run_gate.sh -- run a gate, keep its transcript, and leave a receivable fact.
#
#   ./tools/run_gate.sh svc84_proof_test.sh
#   ./tools/run_gate.sh device_mode_test.sh --some-gate-flag
#
# Wraps any ./tools/<gate>: tees the transcript to
# target/receipts/<gate>.log, writes target/receipts/<gate>.json (see
# tools/gate_receipt.py), and compares it with the previous run for the same
# gate so a verdict that shrank -- fewer legs, fewer OK lines -- is reported
# as drift instead of scrolling past. The gate's exit status is preserved.
#
# Gate scripts are deliberately NOT modified: their own exit paths are the
# thing under test, and a wrapper cannot break them.

set -uo pipefail
cd "$(dirname "$0")/.."

GATE="${1:-}"
if [[ -z "$GATE" ]]; then
    echo "usage: $0 <gate-script> [args...]"
    echo "       e.g. $0 svc84_proof_test.sh"
    exit 2
fi
shift
if [[ ! -x "tools/$GATE" ]]; then
    echo "run_gate: no executable tools/$GATE"
    exit 2
fi

mkdir -p target/receipts
LOG="target/receipts/${GATE%.sh}.log"
RECEIPT="target/receipts/${GATE%.sh}.json"

if [[ -f "$RECEIPT" ]]; then
    cp "$RECEIPT" "$RECEIPT.prev"
fi

set +e
./tools/"$GATE" "$@" 2>&1 | tee "$LOG"
STATUS=${PIPESTATUS[0]}
set -e

python3 tools/gate_receipt.py emit "$GATE" --log "$LOG" --status "$STATUS"

if [[ -f "$RECEIPT.prev" ]]; then
    python3 tools/gate_receipt.py compare "$RECEIPT.prev" "$RECEIPT" || true
fi
rm -f "$RECEIPT.prev"

if [[ "$STATUS" -ne 0 ]]; then
    echo "run_gate: $GATE exited $STATUS (receipt: $RECEIPT)"
    exit "$STATUS"
fi
echo "run_gate: $GATE passed (receipt: $RECEIPT)"
