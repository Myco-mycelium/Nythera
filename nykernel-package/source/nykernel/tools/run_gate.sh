#!/usr/bin/env bash
#
# run_gate.sh -- run a gate, keep its transcript, and leave a diffable fact.
#
#   ./tools/run_gate.sh svc84_proof_test.sh
#   ./tools/run_gate.sh --record device_mode_test.sh   # ...and refresh the
#                                                     # committed reference
#
# Wraps any ./tools/<gate>: tees the transcript to
# target/receipts/<gate>.log, writes target/receipts/<gate>.json (see
# tools/gate_receipt.py), and compares that receipt with a REFERENCE, in this
# order of preference:
#
#   1. the previous run on this machine (target/receipts/<gate>.json)
#   2. the committed known-good receipt (tools/receipts_known_good/<gate>.json)
#   3. nothing -- and then it says so: "drift comparison SKIPPED", because a
#      silent no-op is how a comparison quietly stops comparing.
#
# Case 2 is what makes a FRESH CLONE diffable: without it the first run has
# nothing to be compared against, which is the run most worth checking.
#
# `--record` promotes this run's receipt to tools/receipts_known_good/ (only
# when it PASSED), so the reference is refreshed deliberately, in a diff
# someone reviews, rather than drifting with every local run.
#
# Gate scripts are deliberately NOT modified: their own exit paths are the
# thing under test, and a wrapper cannot break them.

set -uo pipefail
cd "$(dirname "$0")/.."

RECORD=0
ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--record" && ${#ARGS[@]} -eq 0 ]]; then
        RECORD=1
    else
        ARGS+=("$arg")
    fi
done
GATE="${ARGS[0]:-}"
if [[ -z "$GATE" ]]; then
    echo "usage: $0 [--record] <gate-script> [gate args...]"
    echo "       e.g. $0 svc84_proof_test.sh"
    exit 2
fi
GATE_ARGS=("${ARGS[@]:1}")
if [[ ! -x "tools/$GATE" ]]; then
    echo "run_gate: no executable tools/$GATE"
    exit 2
fi

NAME="${GATE%.sh}"
mkdir -p target/receipts
LOG="target/receipts/$NAME.log"
RECEIPT="target/receipts/$NAME.json"
KNOWN_GOOD="tools/receipts_known_good/$NAME.json"

# What will this run be compared against? Decided BEFORE the run, so the
# answer cannot be confused with the receipt this run is about to write.
REFERENCE=""
ORIGIN=""
if [[ -f "$RECEIPT" ]]; then
    REFERENCE="$RECEIPT.prev"
    cp "$RECEIPT" "$REFERENCE"
    ORIGIN="the previous run on this machine"
elif [[ -f "$KNOWN_GOOD" ]]; then
    REFERENCE="$KNOWN_GOOD"
    ORIGIN="the committed known-good receipt ($KNOWN_GOOD)"
fi

set +e
./tools/"$GATE" "${GATE_ARGS[@]}" 2>&1 | tee "$LOG"
STATUS=${PIPESTATUS[0]}
set -e

python3 tools/gate_receipt.py emit "$GATE" --log "$LOG" --status "$STATUS"

if [[ -n "$REFERENCE" ]]; then
    echo "run_gate: comparing against $ORIGIN"
    python3 tools/gate_receipt.py compare "$REFERENCE" "$RECEIPT" || true
else
    echo "run_gate: drift comparison SKIPPED -- no previous receipt for $NAME"
    echo "          and no committed reference at $KNOWN_GOOD."
    echo "          This run's receipt is at $RECEIPT; run this again to diff"
    echo "          against it, or $0 --record $GATE to make it the reference."
fi
rm -f "$RECEIPT.prev"

if [[ "$RECORD" == "1" ]]; then
    if [[ "$STATUS" -eq 0 ]]; then
        python3 tools/gate_receipt.py promote "$GATE"
    else
        echo "run_gate: NOT recording -- $GATE exited $STATUS (a reference must be a PASS)"
    fi
fi

if [[ "$STATUS" -ne 0 ]]; then
    echo "run_gate: $GATE exited $STATUS (receipt: $RECEIPT)"
    exit "$STATUS"
fi
echo "run_gate: $GATE passed (receipt: $RECEIPT)"
