#!/usr/bin/env bash
#
# gate_selfcheck.sh -- prove a proof gate FAILS when the evidence is wrong.
#
#   ./tools/gate_selfcheck.sh [transcript]
#
# Every gate in this tree is a claim of the form "a wrong kernel cannot pass
# me". Nothing checked that claim, and the block-info bug is what that costs:
# a verdict line printed another leg's record, said "matches", and the gate was
# green while a served call was wrong. A gate nobody has ever seen fail is a
# comment with a segfault story.
#
# This tool takes a REAL transcript from tools/svc84_proof_test.sh (it saves
# one to target/svc84-proof-last.log after every pass) and, one at a time,
# deletes a piece of the evidence or moves an expectation, requiring the gate
# to (a) exit non-zero and (b) name the right failure. Then it requires the
# unmutated transcript to still PASS, so a gate that simply fails everything
# cannot pass this either.
#
# Usage:
#   ./tools/svc84_proof_test.sh            # once, to produce the transcript
#   ./tools/gate_selfcheck.sh              # mutate it, expect refusals
#   ./tools/gate_selfcheck.sh path/to/log  # a specific transcript
#
# Exit: 0 every mutation was caught and the clean run passed; 1 otherwise.

set -uo pipefail
cd "$(dirname "$0")/.."

LOG="${1:-target/svc84-proof-last.log}"
GATE=./tools/svc84_proof_test.sh
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

if [[ ! -f "$LOG" ]]; then
    echo "FAIL: no transcript at $LOG"
    echo "      run ./tools/svc84_proof_test.sh once to produce one"
    exit 1
fi
if [[ ! -x "$GATE" ]]; then
    echo "FAIL: $GATE is not executable"
    exit 1
fi

echo "==> baseline: the saved transcript must PASS unmutated"
if "$GATE" --replay "$LOG" >"$TMP/base.log" 2>&1; then
    ok "the unmutated transcript passes (the gate is not failing everything)"
else
    bad "the unmutated transcript FAILS -- the gate or the transcript is broken"
    grep -E "^  FAIL|^FAIL" "$TMP/base.log" | head -6 | sed 's/^/        /'
fi

# name | sed expression applied to a copy | text the refusal must contain
CASES=(
    "ring-3 caller evidence|s/CPL 3/CPL 0/|does not report CPL 3"
    "refusal-matrix cell|s#packed 0xeaeafef3f7f7f7f2/0xf7f7f7f2f2f2eaea/0xf7#packed 0xeaeafef3f7f7f9/0xf7f7f7f2f2f2eaea/0xf7#|refusal matrix did not hold"
    "block-info geometry leg|s/BLOCK-INFO matches/BLOCK-INFO /|block-info leg"
    "supervised display leg|s/DISPLAY matches/DISPLAY /|supervised display"
    "kernel's own verdict|s/-- OK/-- FAILED/|reported the proof as FAILED"
    "the whole verdict|s/service-0x84 proof: .*/proof-line-removed/|never reached its verdict line"
)

i=0
for case in "${CASES[@]}"; do
    i=$((i + 1))
    name="${case%%|*}"
    rest="${case#*|}"
    expr="${rest%%|*}"
    want="${rest#*|}"
    mutant="$TMP/mutant-$i.log"
    sed "$expr" "$LOG" >"$mutant"
    if cmp -s "$LOG" "$mutant"; then
        bad "'$name': the mutation changed nothing (the transcript no longer has that evidence)"
        continue
    fi
    if "$GATE" --replay "$mutant" >"$TMP/out-$i.log" 2>&1; then
        bad "'$name': the gate PASSED a transcript with that evidence removed"
    elif ! grep -qF "$want" "$TMP/out-$i.log"; then
        bad "'$name': the gate failed, but not for the right reason (wanted: $want)"
        grep -E "^  FAIL" "$TMP/out-$i.log" | head -3 | sed 's/^/        /'
    else
        ok "'$name': removed -> the gate refused it"
    fi
done

# The leg-count invariant cannot be exercised by editing the transcript (a
# deleted leg also fails its own check), so move the EXPECTATION instead: if
# raising it by one does not fail, the assertion is not live. The number is
# READ from the gate rather than copied here -- a hard-coded one goes stale the
# first time a leg is added, and then this case fails while the gate is right.
BASE_LEGS="$(sed -n 's/.*SVC84_EXPECTED_LEGS:-\([0-9][0-9]*\).*/\1/p' "$GATE" | head -1)"
echo "==> the leg-count invariant is live (gate expects ${BASE_LEGS:-?} legs)"
if SVC84_EXPECTED_LEGS=$(( BASE_LEGS + 1 )) "$GATE" --replay "$LOG" >"$TMP/legs.log" 2>&1; then
    bad "expecting one more leg than the transcript carries still passed: the leg-count assertion is not reading anything"
elif grep -q "distinct proved leg" "$TMP/legs.log"; then
    ok "expecting one more leg than the transcript carries is refused"
else
    bad "the leg-count mismatch was not named as such"
    grep -E "^  FAIL" "$TMP/legs.log" | head -3 | sed 's/^/        /'
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS self-check(s) did not hold -- the gate can pass wrong evidence"
    exit 1
fi
echo "PASS: the proof gate rejects every mutation tried here, and passes the real transcript"
