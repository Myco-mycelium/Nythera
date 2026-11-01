#!/usr/bin/env bash
#
# runbook_rehearsal.sh -- executes docs/how-to/revoke-a-secure-boot-carrier.md
# end to end, on a FRESH generated dev chain, and asserts the outcome of every
# step the page claims.
#
# Why: a runbook is a claim about tools, and claims about tools rot. Every
# command on that page is cheap (no QEMU, no network), so there is no reason
# for the page to be verified by memory. This script is the page in
# executable form -- custody -> ceremony -> manifest -> artifact check ->
# store check -> fleet check -- and it fails when the page and the tools
# stop agreeing, including when the page still shows a command the tools no
# longer need (the fleet section used to be a `for` loop around --store; it
# is now one --fleet call, and the drift check below refuses the old text).
#
# The chain is genuinely fresh: uefi/tools/trust/ is generated material
# (gitignored) and is DELETED for the run, so every certificate the
# ceremony touches is one build.sh just created with this run's keys. It is
# left in place afterwards rather than restored: uefi/src/ was just signed
# by the new chain, and putting the old certificates back would leave the
# tree holding a chain that does not match its own carriers.
#
# What is asserted, in the page's own order:
#
#   fresh build          the chain is regenerated (root, leaf, stray, and the
#                        custody mode 600 on the signing key)
#   refuse the anchor    revoking the trust anchor is a named FAIL
#   ceremony             PASS, with the manifest, stamp and custody lines
#   manifest             names the artifact's ACTUAL sha256 (recomputed here)
#   artifact check       a 2027 stamp against today is REFUSED as scheduled,
#                        and accepted with --allow-pending (the page's step 4)
#   store check          a store carrying the package passes at --at 2027-01-01
#   fleet check          a clean 2-machine fleet passes with a per-machine
#                        summary; a fleet holding one store without dbx FAILS
#                        and the summary counts the refusal kind
#   --absent             the stray (replacement-shaped) cert is not revoked
#   page consistency     the page contains the flags exercised here and no
#                        longer contains the superseded loop
#
# Usage: ./tools/runbook_rehearsal.sh
# Requires: build.sh's toolchain (cargo, rust-lld), virt-fw-vars, openssl,
# python3. ~1-2 minutes, no boot.

set -uo pipefail
cd "$(dirname "$0")/.."

# The page lives at the repository root, three levels up from this package.
# Resolved once and CHECKED: every consistency grep below passes vacuously
# against a missing file (`grep` prints nothing and says nothing), which
# would turn "the page still agrees with the tools" into a green claim about
# a page that is not there.
DOC=../../../docs/how-to/revoke-a-secure-boot-carrier.md
WORK=target/runbook-rehearsal
TRUST=uefi/tools/trust
ROOTCERT=$TRUST/carrier-dev-cert.pem
ROOTKEY=$TRUST/carrier-dev-key.pem
LEAF=$TRUST/carrier-dev-leaf-cert.pem
STRAY=$TRUST/carrier-neg-stray-cert.pem

for f in ./build.sh ./tools/sign_dbx_update_ceremony.sh ./tools/verify_dbx_store.py "$DOC"; do
    [[ -s "$f" ]] || { echo "FAIL: prerequisite missing or empty: $f"; exit 1; }
done
[[ -x ./build.sh ]] || { echo "FAIL: prerequisite not executable: ./build.sh"; exit 1; }
[[ -x ./tools/sign_dbx_update_ceremony.sh ]] || { echo "FAIL: prerequisite not executable: ./tools/sign_dbx_update_ceremony.sh"; exit 1; }
[[ -x ./tools/verify_dbx_store.py ]] || { echo "FAIL: prerequisite not executable: ./tools/verify_dbx_store.py"; exit 1; }
for tool in openssl python3 sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || { echo "FAIL: required tool missing: $tool"; exit 1; }
done
VIRT="$(command -v virt-fw-vars 2>/dev/null || echo "$HOME/.local/bin/virt-fw-vars")"
[[ -x "$VIRT" ]] || { echo "FAIL: virt-fw-vars missing (pip name: virt-firmware)"; exit 1; }

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# expect_case <label> <ok|fail> <needle> <cmd...>
expect_case() {
    local label="$1" want="$2" needle="$3"
    shift 3
    local out="$TMP_OUT/$label.log"
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

rm -rf "$WORK"
mkdir -p "$WORK"
TMP_OUT="$WORK/logs"
mkdir -p "$TMP_OUT"

echo "==> rehearsal of $DOC"
echo "==> step 0: fresh generated chain (the page's \"a built tree\" precondition)"
rm -rf "$TRUST"
mkdir -p "$TRUST"
if ./build.sh --qemu-test >"$TMP_OUT/build.log" 2>&1; then
    ok "build: ./build.sh --qemu-test completed on an empty trust directory"
else
    bad "build: ./build.sh --qemu-test failed (see $TMP_OUT/build.log)"
    tail -20 "$TMP_OUT/build.log" | sed 's/^/        /'
    exit 1
fi
missing=0
for f in "$ROOTCERT" "$ROOTKEY" "$LEAF" "$STRAY"; do
    [[ -s "$f" ]] || { bad "fresh chain: $f was not generated"; missing=1; }
done
[[ "$missing" -eq 0 ]] && ok "fresh chain: root, leaf and stray certificates generated by this run"
# The page's precondition table promises the key is mode 600; the ceremony
# refuses otherwise, so a fresh chain that violates it would fail later with
# a message about custody instead of about the build.
MODE="$(stat -c %a "$ROOTKEY" 2>/dev/null || echo '?')"
case "$MODE" in
    600) ok "fresh chain: signing key mode 600 (the page's precondition)" ;;
    *)   bad "fresh chain: signing key mode is $MODE, the page's table says 600 and the ceremony requires no group/world bits" ;;
esac

# --- the page's step 1: naming the anchor is a refusal --------------------
echo "==> step 1: the anchor is refused as a revocation target"
expect_case anchor-refused fail "the revocation target IS the trust anchor" \
    ./tools/sign_dbx_update_ceremony.sh --revoke "$ROOTCERT" --anchor "$ROOTCERT" \
    --out "$WORK/anchor"

# --- the page's step 2: run the ceremony on the (fresh) leaf --------------
echo "==> step 2: the ceremony on the fresh leaf"
CEREMONY_OUT="$WORK/revocation"
expect_case ceremony ok "PASS: signing/distribution ceremony complete" \
    ./tools/sign_dbx_update_ceremony.sh \
        --revoke "$LEAF" \
        --kek-cert "$ROOTCERT" \
        --kek-key "$ROOTKEY" \
        --enrolled-kek "$ROOTCERT" \
        --anchor "$ROOTCERT" \
        --previous-stamp 2026-01-01 \
        --out "$CEREMONY_OUT"

PACKAGE="$CEREMONY_OUT/dbx-update.auth2"
MANIFEST="$CEREMONY_OUT/update-manifest.txt"
if [[ -s "$PACKAGE" && -s "$MANIFEST" ]]; then
    ok "ceremony: package and update-manifest.txt both written"
else
    bad "ceremony: expected $PACKAGE and $MANIFEST"
fi

# --- the page's step 3: the manifest is the thing a second person checks --
echo "==> step 3: the manifest names the artifact actually produced"
ACTUAL_SHA="$(sha256sum "$PACKAGE" | cut -d' ' -f1)"
if grep -qF "$ACTUAL_SHA" "$MANIFEST"; then
    ok "manifest: carries the package's real sha256 ($ACTUAL_SHA)"
else
    bad "manifest: does not carry the recomputed package sha256 ($ACTUAL_SHA) -- the page's step 3 claim is false"
    sed 's/^/        /' "$MANIFEST" | head -12
fi

# --- the page's step 4: a future stamp is refused, not silently accepted --
echo "==> step 4: the artifact check (scheduled revocation is not in force)"
expect_case artifact-scheduled fail "revoked only FROM 2027-01-01" \
    ./tools/verify_dbx_store.py --dbx "$PACKAGE" --cert "$LEAF"
expect_case artifact-pending-ok ok "PASS: 1 artifact(s) revoke" \
    ./tools/verify_dbx_store.py --dbx "$PACKAGE" --cert "$LEAF" --allow-pending

# --- the page's steps 5-6: a store that carries it, and a fleet audit ------
echo "==> steps 5-6: compose a store carrying the package, then audit a fleet"
VARS_TMPL=uefi/tools/OVMF_VARS_4M.fd
if [[ -s "$VARS_TMPL" ]]; then
    GOOD="$WORK/store-enforcing.fd"
    cp "$VARS_TMPL" "$GOOD"
    if "$VIRT" --inplace "$GOOD" --set-dbx "$PACKAGE" -l ERROR >"$TMP_OUT/compose.log" 2>&1; then
        ok "store: the ceremony's package is accepted by the store tooling (--set-dbx)"
    else
        bad "store: virt-fw-vars refused the ceremony's package"
        tail -10 "$TMP_OUT/compose.log" | sed 's/^/        /'
    fi
    # The rollout is audited at a date on which the revocation is in force:
    # --at is the page's tool for exactly that (the package's stamp is 2027).
    mkdir -p "$WORK/fleet-clean" "$WORK/fleet-mixed"
    cp "$GOOD" "$WORK/fleet-clean/machine-01.fd"
    cp "$GOOD" "$WORK/fleet-clean/machine-02.fd"
    cp "$GOOD" "$WORK/fleet-mixed/machine-01.fd"
    cp "$VARS_TMPL" "$WORK/fleet-mixed/machine-02.fd"   # never received it

    expect_case fleet-clean ok "2 machine(s): 2 enforcing" \
        ./tools/verify_dbx_store.py --fleet "$WORK/fleet-clean" --cert "$LEAF" \
        --at 2027-01-01 --absent "$STRAY"

    expect_case fleet-mixed fail "1 enforcing, 1 no-dbx-variable" \
        ./tools/verify_dbx_store.py --fleet "$WORK/fleet-mixed" --cert "$LEAF" \
        --at 2027-01-01

    # --absent in the failing direction: the "replacement" revoked too.
    BAD="$WORK/store-overbroad.fd"
    cp "$VARS_TMPL" "$BAD"
    if python3 tools/patch_carrier_negatives.py make-dbx-siglist "$STRAY" "$WORK/stray-list.der" >/dev/null 2>&1 \
       && python3 tools/patch_carrier_negatives.py make-dbx-auth-update "$WORK/stray-list.der" 2020 "$WORK/stray-auth2.der" >/dev/null 2>&1 \
       && "$VIRT" --inplace "$BAD" --set-dbx "$WORK/stray-auth2.der" --add-dbx-cert d719b2cb-3d3a-4596-a3bc-dad00e67656f "$STRAY" -l ERROR >/dev/null 2>&1; then
        expect_case absent-overbroad fail "revokes it too" \
            ./tools/verify_dbx_store.py --store "$BAD" --cert "$LEAF" \
            --at 2027-01-01 --absent "$STRAY"
    else
        bad "absent-check: could not compose the over-broad store"
    fi
else
    bad "store: $VARS_TMPL missing -- the page's step 6 cannot be rehearsed"
fi

# --- the page still shows what the tools now do --------------------------
echo "==> page consistency: the runbook documents the commands exercised here"
for needle in "--fleet" "--enrolled-kek" "--allow-pending" "--absent" "--previous-stamp"; do
    if grep -qF -- "$needle" "$DOC"; then
        ok "page: documents $needle"
    else
        bad "page: does not document $needle, which this rehearsal exercised"
    fi
done
if grep -qF 'for s in collected/*.fd' "$DOC"; then
    bad "page: still shows the superseded per-store loop; the fleet audit is one --fleet call now"
else
    ok "page: no superseded per-store loop remains"
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS runbook step(s) did not behave as the page claims -- the page and the tools have drifted (logs in $TMP_OUT)"
    exit 1
fi
echo "PASS: runbook rehearsed on a fresh chain -- anchor refusal, ceremony, manifest sha256, scheduled-vs-in-force artifact check, a store that carries the package, a clean and a mixed fleet audit with per-machine summaries, the over-broad --absent refusal, and the page's own command text all hold"
exit 0
