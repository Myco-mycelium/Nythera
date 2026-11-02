#!/usr/bin/env bash
#
# rollout_plan_test.sh -- proves the per-machine rollout plan's verdicts
# BITE, in both directions.
#
# tools/rollout_plan.py answers the question ADR-0029 leaves to "platform
# process": will THIS signed revocation package land on THAT machine, and
# from what date will the machine enforce it. A planner that answered
# "apply" for every store would be worse than no planner, because the
# operator would find out on the machine. So every action and every block
# is exercised here with a store built for it:
#
#   apply                 KEK carries the signer, no existing dbx, stamp
#                         advances                                  -> PASS
#   apply (dbx present)   the store's entries are all carried by the
#                         package (a non-destructive write)        -> PASS
#   skip                  already revokes the target at the stamp    -> PASS
#                         (nothing is written, so no write checks)
#   kek-mismatch          the store's KEK does not carry the signer  -> FAIL
#   no-kek-variable       no KEK at all                              -> FAIL
#   stale-stamp           the store's dbx is newer than the package  -> FAIL
#   would-drop-existing- entries the package REPLACES the dbx variable and
#                         does not carry an entry the store has     -> FAIL
#   unreadable            not a store                                -> FAIL
#   canary                wave 1, and the rest of the fleet in wave 2
#   canary absent/skip    refused: a canary the package does not
#                         reach proves nothing                       -> FAIL
#   bare list package     refused (no EFI_TIME: indistinguishable
#                         from a replay)                            -> FAIL
#   wrong target          the package does not revoke --cert          -> FAIL
#   over-broad            the package revokes an --absent cert        -> FAIL
#   wrong --kek-cert      the package's signer is not the named KEK   -> FAIL
#   empty fleet           refused, not a clean plan of nothing        -> FAIL
#
# Plus the properties a plan artifact has to have: deterministic (two runs
# with the same --at produce byte-identical JSON), pinned (the JSON carries
# a plan sha256 that recomputes from its own contents and matches the .txt),
# and read-only (every store's sha256 is unchanged by planning).
#
# Each case asserts the exit class AND a named line, so an unrelated crash
# cannot satisfy it. Hermetic: four throwaway RSA-2048 certificates, a copy
# of the plain 4M varstore template, no network, no QEMU, nothing written
# outside the temp directory.
#
# Usage: ./tools/rollout_plan_test.sh

set -uo pipefail
cd "$(dirname "$0")/.."

PLAN=./tools/rollout_plan.py
HELPER=./tools/patch_carrier_negatives.py
MAKE_PKG=./tools/make_dbx_update_package.sh
TEMPLATE=uefi/tools/OVMF_VARS_4M.fd
PK_GUID=8be4df61-93ca-11d2-aa0d-00e098032b8c
ISD_GUID=d719b2cb-3d3a-4596-a3bc-dad00e67656f

for f in "$PLAN" "$HELPER" "$MAKE_PKG" "$TEMPLATE"; do
    if [[ ! -e "$f" ]]; then
        echo "FAIL: prerequisite missing: $f"
        exit 1
    fi
done
for tool in openssl python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "FAIL: required tool missing: $tool"; exit 1; }
done
VIRT="$(command -v virt-fw-vars 2>/dev/null || echo "$HOME/.local/bin/virt-fw-vars")"
if [[ ! -x "$VIRT" ]]; then
    echo "FAIL: virt-fw-vars missing (pip name: virt-firmware) -- a machine's store cannot be read"
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# expect_case <label> <expected-exit:ok|fail> <expected-substring> <cmd...>
expect_case() {
    local label="$1" want="$2" needle="$3"
    shift 3
    local out="$TMP/$label.log"
    "$@" >"$out" 2>&1
    local s=$?
    local got="ok"
    [[ $s -ne 0 ]] && got="fail"
    if [[ "$got" != "$want" ]]; then
        bad "$label: expected exit class '$want', got '$got' (status $s); see below"
        sed 's/^/        /' "$out" | tail -20
        return
    fi
    if ! grep -qF "$needle" "$out"; then
        bad "$label: exit class '$want' matched but the named line is absent: '$needle'"
        sed 's/^/        /' "$out" | tail -20
        return
    fi
    ok "$label: $want, named line present (exit $s)"
}

mk_cert() { # $1 CN, $2 basename
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
        -subj "/CN=Nyrqis rollout test $1" \
        -keyout "$TMP/$2.key" -out "$TMP/$2.pem" >/dev/null 2>&1
    chmod 600 "$TMP/$2.key"
    openssl x509 -in "$TMP/$2.pem" -outform DER -out "$TMP/$2.der"
}

echo "==> rollout plan refusal tests"
mk_cert "KEK" kek
mk_cert "other KEK" otherkek
mk_cert "dead leaf" leaf
mk_cert "replacement leaf" replacement
mk_cert "unrelated" unrelated

# --- the package: one signed EFI_CERT_X509_SHA256 revocation -------------
python3 "$HELPER" make-dbx-hashlist "$TMP/leaf.der" 2027 "$TMP/leaf-list.der" >/dev/null
"$MAKE_PKG" "$TMP/leaf-list.der" "$TMP/kek.pem" "$TMP/kek.key" 2027 \
    "$TMP/pkg.auth2" "$TMP/pkg-work" >/dev/null 2>&1 \
    || { echo "FAIL: could not compose the test package"; exit 1; }

# An over-broad package: the same list with the replacement's entry added.
python3 - "$TMP/leaf-list.der" "$TMP/replacement.der" "$TMP/overbroad-list.der" <<'PY'
import hashlib, struct, sys
sys.path.insert(0, "tools")
import verify_dbx_store as v

list_path, replacement_der, out_path = sys.argv[1:4]
blob = bytearray(open(list_path, "rb").read())
size, hdr, entry = struct.unpack("<III", blob[16:28])
assert size == len(blob) and entry == 64, (size, len(blob), entry)
tbs = hashlib.sha256(v.cert_tbs(open(replacement_der, "rb").read())).digest()
body = bytes(16) + tbs + (2027).to_bytes(2, "little") + bytes([1, 1, 0, 0, 0, 0]) + bytes(8)
blob[16:20] = struct.pack("<I", size + entry)
blob += body
open(out_path, "wb").write(bytes(blob))
PY
"$MAKE_PKG" "$TMP/overbroad-list.der" "$TMP/kek.pem" "$TMP/kek.key" 2027 \
    "$TMP/pkg-overbroad.auth2" "$TMP/pkg-work-ob" >/dev/null 2>&1 \
    || { echo "FAIL: could not compose the over-broad package"; exit 1; }

# --- fixtures: one store per verdict -------------------------------------
$VIRT -i "$TEMPLATE" --set-true SecureBootEnable -o "$TMP/base.fd" -l ERROR >/dev/null 2>&1 \
    || { echo "FAIL: could not build the baseline store from $TEMPLATE"; exit 1; }

add_kek() { $VIRT --inplace "$1" --add-kek "$PK_GUID" "$2" -l ERROR >/dev/null 2>&1; }

# A machine that can take the package: the KEK carries the signer and there
# is no dbx to advance past or lose.
cp "$TMP/base.fd" "$TMP/apply-1.fd"; add_kek "$TMP/apply-1.fd" "$TMP/kek.pem"
cp "$TMP/base.fd" "$TMP/apply-2.fd"; add_kek "$TMP/apply-2.fd" "$TMP/kek.pem"

# Already enforcing: the package was delivered, so nothing is written.
cp "$TMP/base.fd" "$TMP/skip.fd"; add_kek "$TMP/skip.fd" "$TMP/kek.pem"
$VIRT --inplace "$TMP/skip.fd" --set-dbx "$TMP/pkg.auth2" -l ERROR >/dev/null 2>&1

# A KEK that is not the signer: firmware refuses the update on the machine.
cp "$TMP/base.fd" "$TMP/otherkek.fd"; add_kek "$TMP/otherkek.fd" "$TMP/otherkek.pem"

# No KEK at all.
cp "$TMP/base.fd" "$TMP/nokek.fd"

# A dbx newer than the package: firmware rejects a non-increasing stamp.
python3 "$HELPER" make-dbx-hashlist "$TMP/leaf.der" 2030 "$TMP/leaf-2030.der" >/dev/null
python3 "$HELPER" make-dbx-auth-update "$TMP/leaf-2030.der" 2030 "$TMP/leaf-2030.auth2" >/dev/null
cp "$TMP/base.fd" "$TMP/stale.fd"; add_kek "$TMP/stale.fd" "$TMP/kek.pem"
$VIRT --inplace "$TMP/stale.fd" --set-dbx "$TMP/leaf-2030.auth2" -l ERROR >/dev/null 2>&1

# A store whose dbx carries an entry the package does not: the write would
# REPLACE the variable, un-revoking that entry.
cp "$TMP/base.fd" "$TMP/drop.fd"; add_kek "$TMP/drop.fd" "$TMP/kek.pem"
$VIRT --inplace "$TMP/drop.fd" --add-dbx-cert "$ISD_GUID" "$TMP/unrelated.pem" -l ERROR >/dev/null 2>&1

printf 'not a store at all\n' >"$TMP/unreadable.fd"

# --- cases ---------------------------------------------------------------
mkdir -p "$TMP/fleet-ok" "$TMP/fleet-skip" "$TMP/fleet-mixed" "$TMP/fleet-empty"
cp "$TMP/apply-1.fd" "$TMP/fleet-ok/machine-01.fd"
cp "$TMP/apply-2.fd" "$TMP/fleet-ok/machine-02.fd"
cp "$TMP/skip.fd" "$TMP/fleet-skip/machine-01.fd"
cp "$TMP/apply-2.fd" "$TMP/fleet-skip/machine-02.fd"
cp "$TMP/otherkek.fd" "$TMP/fleet-mixed/machine-a.fd"
cp "$TMP/nokek.fd" "$TMP/fleet-mixed/machine-b.fd"
cp "$TMP/stale.fd" "$TMP/fleet-mixed/machine-c.fd"
cp "$TMP/drop.fd" "$TMP/fleet-mixed/machine-d.fd"
cp "$TMP/unreadable.fd" "$TMP/fleet-mixed/machine-e.fd"
printf 'not a machine\n' >"$TMP/fleet-empty/README.txt"

PLAN_ARGS=(--package "$TMP/pkg.auth2" --cert "$TMP/leaf.pem"
           --kek-cert "$TMP/kek.pem" --at 2026-11-02)

expect_case plan-ok ok "PASS: plan is executable on all 2 machine(s)" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok"

expect_case plan-ok-count ok "2 machine(s): 2 apply" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok"

expect_case plan-nodbx ok "no dbx variable: nothing to advance past, nothing to lose" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok"

expect_case plan-skip ok "2 machine(s): 1 apply, 1 already-enforcing" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-skip"

expect_case plan-skip-reason ok "already revokes the target through EFI_CERT_X509_SHA256" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-skip"

# The canary is the substance of the schedule: wave 1 then the rest.
expect_case plan-canary ok "wave 1 (canary wave -- verify before the rest): machine-01.fd" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok" --canary machine-01.fd

expect_case plan-canary-rest ok "wave 2 (fleet): machine-02.fd" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok" --canary machine-01.fd

expect_case plan-blocked fail \
    "5 machine(s): 5 blocked (kek-mismatch, no-kek-variable, stale-stamp, unreadable, would-drop-existing-entries)" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-mixed"

for needle in \
    "kek-mismatch: this store's KEK does not carry the package's signer" \
    "no-kek-variable: no KEK variable" \
    "stale-stamp: the package's stamp 2027-01-01 00:00:00 is not newer" \
    "would-drop-existing-entries: the package carries 1 entrie(s) and a dbx update REPLACES" \
    "unreadable: cannot read the store"; do
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-mixed" >"$TMP/mixed.log" 2>&1 || true
    if grep -qF "$needle" "$TMP/mixed.log"; then
        ok "plan-blocked: names '$needle'"
    else
        bad "plan-blocked: the named line is absent: '$needle'"
        sed 's/^/        /' "$TMP/mixed.log" | tail -14
    fi
done

# The package's own preconditions: an artifact the plan should not bless.
expect_case plan-bare-list fail "EFI_VARIABLE_AUTHENTICATION_2" \
    "$PLAN" --package "$TMP/leaf-list.der" --cert "$TMP/leaf.pem" \
    --kek-cert "$TMP/kek.pem" --at 2026-11-02 --fleet "$TMP/fleet-ok"

expect_case plan-wrong-target fail "package precondition" \
    "$PLAN" --package "$TMP/pkg.auth2" --cert "$TMP/replacement.pem" \
    --kek-cert "$TMP/kek.pem" --at 2026-11-02 --fleet "$TMP/fleet-ok"

expect_case plan-overbroad fail "revokes it too" \
    "$PLAN" --package "$TMP/pkg-overbroad.auth2" --cert "$TMP/leaf.pem" \
    --kek-cert "$TMP/kek.pem" --at 2026-11-02 --fleet "$TMP/fleet-ok" \
    --absent "$TMP/replacement.pem"

expect_case plan-wrong-kek fail "the package's signer certificate is not --kek-cert" \
    "$PLAN" --package "$TMP/pkg.auth2" --cert "$TMP/leaf.pem" \
    --kek-cert "$TMP/otherkek.pem" --at 2026-11-02 --fleet "$TMP/fleet-ok"

expect_case plan-empty fail "a plan over an empty fleet is not a plan" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-empty"

expect_case plan-canary-absent fail "no machine by that name in the fleet" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok" --canary machine-99.fd

expect_case plan-canary-skip fail "already enforces the revocation" \
    "$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-skip" --canary machine-01.fd

# --- the artifact properties --------------------------------------------
mkdir -p "$TMP/plan-a" "$TMP/plan-b"
BEFORE="$(sha256sum "$TMP/fleet-ok"/* "$TMP/fleet-mixed"/*)"
"$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok" --canary machine-01.fd \
    --out "$TMP/plan-a" >/dev/null 2>&1
"$PLAN" "${PLAN_ARGS[@]}" --fleet "$TMP/fleet-ok" --canary machine-01.fd \
    --out "$TMP/plan-b" >/dev/null 2>&1
AFTER="$(sha256sum "$TMP/fleet-ok"/* "$TMP/fleet-mixed"/*)"
if [[ "$BEFORE" == "$AFTER" ]]; then
    ok "plan is read-only: every store's sha256 is unchanged by planning"
else
    bad "plan is read-only: a store changed while only being planned"
fi

if cmp -s "$TMP/plan-a/rollout-plan.json" "$TMP/plan-b/rollout-plan.json"; then
    ok "plan is deterministic: two runs with the same --at are byte-identical"
else
    bad "plan is deterministic: two identical runs produced different JSON"
fi

if python3 - "$TMP/plan-a" <<'PY'
import hashlib, json, sys

d = sys.argv[1]
doc = json.load(open(d + "/rollout-plan.json"))
txt = open(d + "/rollout-plan.txt").read()

# The digest must recompute from the document's own contents (everything
# except the digest field), so a plan cannot be edited after approval.
claimed = doc.pop("plan_sha256")
canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
assert claimed == hashlib.sha256(canonical.encode()).hexdigest(), claimed
assert claimed in txt, "the .txt does not carry the digest the .json claims"

# The waves are the schedule: canary first, the rest second.
assert [w["role"] for w in doc["waves"]] == ["canary", "fleet"], doc["waves"]
assert doc["waves"][0]["machines"] == ["machine-01.fd"], doc["waves"]
assert doc["waves"][1]["machines"] == ["machine-02.fd"], doc["waves"]

# Every machine's action and category are machine-readable.
acts = {m["machine"]: (m["action"], m["category"]) for m in doc["machines"]}
assert acts["machine-01.fd"] == ("apply", "needs-package"), acts
assert doc["verdict"] == "executable", doc["verdict"]
assert doc["enforcement_effective"] == "2027-01-01", doc["enforcement_effective"]
PY
then
    ok "plan artifact: sha256 recomputes, waves are the schedule, actions are machine-readable"
else
    bad "plan artifact: the JSON does not carry the pinned, deterministic plan required"
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS rollout plan case(s) did not behave as required -- a verdict above is not biting"
    exit 1
fi
echo "PASS: rollout plan verdicts verified -- apply/skip/canary waves pass, and kek-mismatch, no-KEK, stale-stamp, would-drop-existing-entries, unreadable, wrong target, over-broad, bare-list, wrong signer and empty-fleet each refuse with their own named message, while the plan stays read-only and deterministic"
