#!/usr/bin/env bash
#
# qemu_uefi_ms_secboot_test.sh -- the secure-boot-enforcing twin of
# qemu_uefi_boot_test.sh. Same kernel+stub build, but the firmware is
# the enforcing OVMF (-4M.ms) CODE image and the variable store is
# FRESHLY composed from the PLAIN variable-store template with the
# carrier chain itself: the ADR-0014 root pair as PK/KEK, root+leaf
# certificates in db, SecureBootEnable on. The LEAF cert additionally
# goes into MokList under shim's SHIM_LOCK_GUID (ADR-0015's content
# decision: a user enrollment carries the LEAF signer, never the root
# anchor -- enrolling the root would grant every future leaf it ever
# issues), so the positive leg also proves the stub reads a REAL shim
# MokList variable on real enforcing firmware and names its facts. The plain (not .ms)
# template is deliberate: virt-fw-vars can only ADD entries to a
# template it reads, never remove them, so a .ms-template copy would
# keep the template's Microsoft authorities in KEK/db -- the written
# store would not be the store this gate claims to prove against.
# The stub is then Authenticode-signed with
# the leaf key (sbsign) and installed on the ESP, so the FIRMWARE's
# verification -- not ours -- is the gate the stub passes, and the
# stub's own carrier walk (which anchors the same root) still enforces
# the kernel image inside it. FOUR boots assert all directions:
#
# positive: signed stub under enforcement -> firmware accepts it, the
#   stub reads SecureBoot=1 with PK present (not SetupMode), the
#   carrier chain verifies over the kernel, the stub ALSO walks the
#   real MokList variable and names its leaf entry covering the
#   just-verified chain (the MOK path extending the embedded anchor,
#   both facts pinned), and the boot reaches Ready
#   (host exit code 33, the same contract as the non-ms gate).
# negative (unsigned): the UNSIGNED stub against the same enforcing
#   store -> the firmware refuses it at the boot manager ("Access
#   Denied") and the boot never reaches the stub (host timeout
#   catches the idle loop).
# negative (tampered): the SAME signed image with ONE flipped byte
#   inside a hash-covered section -> the signature stops matching the
#   bytes it covers and the firmware must refuse it the same way;
#   enforcement covers signed-but-modified images, not only images
#   with no signature at all.
# negative (revoked): a SECOND enforcing store, identical enrollment
#   PLUS the LEAF signer's To-Be-Signed hash as an EFI_CERT_X509_SHA256
#   dbx entry (revoked 2020, so any boot clock after that date hits it)
#   -- the exact real-world hash-typed revocation form Microsoft dbx
#   updates use, delivered to the store through the authenticated-update
#   header virt-fw's --set-dbx parses. The leaf-signed stub (and the
#   MokList-enrolled signer it rides on) must be REFUSED: either by the
#   firmware itself (Access Denied, the boot manager's loop), or -- if
#   this OVMF build's dbx hash check is absent -- by the STUB's own
#   revocation walk over the REAL firmware-served dbx variable. Either
#   refusal is a PASS because both prove the same fact: the revocation
#   leg is wired through real firmware bytes and enforced, and every
#   refusal names its stage.
#
# This is what CI should run to prove the ms OVMF path, the same way
# qemu_uefi_boot_test.sh proves the default OVMF path.

set -euo pipefail
cd "$(dirname "$0")/.."

FAKECODE=uefi/tools/OVMF_CODE_4M.ms.fd
VARS_TMPL=uefi/tools/OVMF_VARS_4M.fd
for f in "$FAKECODE" "$VARS_TMPL"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: OVMF image missing: $f"
        exit 1
    fi
done

# Same build order and stub path as qemu_uefi_boot_test.sh: kernel
# BEFORE stub, because the stub embeds the kernel image bytes.
./build.sh --qemu-test
if ! command -v rust-lld >/dev/null 2>&1; then
    _sysroot="$(rustc --print sysroot 2>/dev/null || true)"
    if [[ -n "$_sysroot" ]]; then
        _rust_lld="$(find "$_sysroot" -name rust-lld -type f 2>/dev/null | head -n1)"
        if [[ -n "$_rust_lld" ]]; then
            export PATH="$(dirname "$_rust_lld"):$PATH"
        fi
    fi
fi
(cd uefi && RUSTC_BOOTSTRAP=1 cargo build --target x86_64-unknown-uefi)
STUB_EFI=uefi/target/x86_64-unknown-uefi/debug/nyrqis_uefi_stub.efi
if [[ ! -f "$STUB_EFI" ]]; then
    echo "FAIL: stub built but not found at $STUB_EFI"
    exit 1
fi

for tool in sbsign sbverify mformat mmd mcopy qemu-system-x86_64 python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "FAIL: required tool missing: $tool"
        exit 1
    }
done
VIRT="$(command -v virt-fw-vars 2>/dev/null || echo "$HOME/.local/bin/virt-fw-vars")"
if [[ ! -x "$VIRT" ]]; then
    echo "FAIL: virt-fw-vars missing (pip name: virt-firmware) -- enrollment cannot be composed"
    exit 1
fi

# --- compose the enforcing store from the PLAIN template ---
# See the header: the .ms template's own keys would MERGE into the
# composition (virt-fw-vars adds, never removes), so the store starts
# from the plain template and receives exactly the carrier chain.
ROOT_CERT=uefi/tools/trust/carrier-dev-cert.pem
LEAF_CERT=uefi/tools/trust/carrier-dev-leaf-cert.pem
LEAF_KEY=uefi/tools/trust/carrier-dev-leaf-key.pem
for f in "$ROOT_CERT" "$LEAF_CERT" "$LEAF_KEY"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: carrier trust material missing (run the build first): $f"
        exit 1
    fi
done
GLV=8be4df61-93ca-11d2-aa0d-00e098032b8c   # EFI_GLOBAL_VARIABLE
ISD=d719b2cb-3d3a-4596-a3bc-dad00e67656f   # EFI_IMAGE_SECURITY_DATABASE_GUID
# shim's SHIM_LOCK_GUID, the vendor GUID shim reads every MokListRT/
# MokNew/MokAuth state variable from -- verified line-for-line against
# rhboot/shim lib/guid.c, the same value virt-fw-vars' Shim GUID and
# the stub's efi.rs pin.
SHIM=605dab50-e046-4300-abb6-3dd810dd8b23
STORE=uefi/dist/VARS.ms-secboot.fd
cp "$VARS_TMPL" "$STORE"
# NOTE: --inplace (or -o) is REQUIRED to write back; virt-fw-vars with
# -i alone only PLANS the changes -- the store on disk keeps the
# template as-is, and the firmware then refuses even a correctly-signed
# stub.
"$VIRT" --inplace "$STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --add-mok "$SHIM" "$LEAF_CERT" \
    --sb > /dev/null
echo "==> enrolled enforcing store: PK/KEK anchored on the carrier root, root+leaf in db, SecureBootEnable ON, leaf in MokList"
echo "==> composed store shape (what the firmware will actually read):" && \
    "$VIRT" -i "$STORE" -p 2>/dev/null | grep -E "PK |KEK |db |MokList|SecureBoot" || true

# --- Authenticode-sign the stub with the leaf key ---
# The signer certificate must be one the firmware's db can bind
# directly: the leaf is enrolled verbatim, so AuthenticodeVerify
# matches the signer on sight.
SIGNED_STUB=uefi/dist/nyrqis_uefi_stub-signed.efi
sbsign --key "$LEAF_KEY" --cert "$LEAF_CERT" "$STUB_EFI" --output "$SIGNED_STUB" > /dev/null
sbverify --list "$SIGNED_STUB" | head -4

# --- ONE-byte tamper of the SAME signed image ---
# Flips one bit INSIDE the first hash-covered section (mid-way through
# its raw data), leaving every header byte and the certificate table
# untouched: the PE still parses, the signature is still present, and
# the signed content no longer matches the bytes it covers -- exactly
# the damage enforcement must catch on a signed-but-modified image.
TAMPERED_STUB=uefi/dist/nyrqis_uefi_stub-tampered.efi
python3 - "$SIGNED_STUB" "$TAMPERED_STUB" <<'PYEOF'
import struct, sys
src, dst = sys.argv[1], sys.argv[2]
data = bytearray(open(src, 'rb').read())
pe = struct.unpack_from('<I', data, 0x3c)[0]
assert data[pe:pe + 4] == b'PE\x00\x00', 'tamper probe: not a PE image'
coff = pe + 4
nsec = struct.unpack_from('<H', data, coff + 2)[0]
optsz = struct.unpack_from('<H', data, coff + 16)[0]
table = coff + 20 + optsz
chosen = None
for i in range(nsec):
    s = table + 40 * i
    name, _vsize, _va, rawsize, ptr = struct.unpack_from('<8sIIII', data, s)
    if rawsize > 0:
        chosen = (name.rstrip(b'\x00').decode('latin-1'), ptr, rawsize)
        break
assert chosen is not None, 'tamper probe: no raw section found'
name, ptr, rawsize = chosen
off = ptr + rawsize // 2
assert off + 16 < len(data), 'tamper probe: mid-section reaches the cert area'
before = data[off]
data[off] ^= 0x80
open(dst, 'wb').write(data)
print(f'tamper: flipped top bit at {name}[{off}] '
      f'(0x{before:02x} -> 0x{data[off]:02x}), size unchanged ({len(data)})')
PYEOF
cmp -s "$SIGNED_STUB" "$TAMPERED_STUB" && {
    echo "FAIL: tampered stub is identical to the signed one"
    exit 1
}

# --- three ESP images: signed, unsigned, and tampered-signed ---
mk_esp() {
    rm -f "$1"
    mkdir -p uefi/dist/EFI/BOOT
    mformat -i "$1" -C -T 131072
    mmd -i "$1" ::EFI ::EFI/BOOT
    mcopy -i "$1" "$2" ::EFI/BOOT/BOOTX64.EFI
}
ESP_SIGNED=uefi/dist/esp-signed.img
ESP_UNSIGNED=uefi/dist/esp-unsigned.img
ESP_TAMPERED=uefi/dist/esp-tampered.img
mk_esp "$ESP_SIGNED" "$SIGNED_STUB"
mk_esp "$ESP_UNSIGNED" "$STUB_EFI"
mk_esp "$ESP_TAMPERED" "$TAMPERED_STUB"

boot_qemu() { # $1 esp image, $2 serial log, $3 timeout seconds, $4 store (default: the enforcing store)
    local STORE_ELSE="${4:-$STORE}"
    timeout "$3" qemu-system-x86_64 \
        -machine q35 \
        -m 256 \
        -drive if=pflash,format=raw,readonly=on,file="$FAKECODE" \
        -drive if=pflash,format=raw,file="$STORE_ELSE" \
        -drive format=raw,file="$1" \
        -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
        -serial stdio \
        -debugcon file:target/qemu-uefi-ms-ovmf.log \
        -global isa-debugcon.iobase=0x402 \
        -display none \
        -no-reboot \
        > "$2" 2>&1
}

set +e

# --- positive: the signed stub crosses the firmware's own gate ---
echo "==> positive boot: signed stub under enforcement"
boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-positive.log 480
POS_STATUS=$?
echo "--- positive serial (key lines) ---"
grep -a -E "SecureBoot|PK variable|carrier verify|Ready|Access Denied" \
    target/qemu-uefi-ms-secboot-positive.log || true
echo "-----------------------------------"
POS_OK=0
if [[ "$POS_STATUS" -eq 33 ]]; then
    if grep -aq "SecureBoot=1" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "PK variable PRESENT" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "carrier verify OK" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "MokList: walked" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "ALSO covers the just-verified chain" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "boot phase = 7 (Ready)" target/qemu-uefi-ms-secboot-positive.log; then
        POS_OK=1
    else
        echo "note: boot reached Ready but a required secboot marker line is missing (see log)"
    fi
elif [[ "$POS_STATUS" -eq 124 ]]; then
    echo "FAIL: positive boot timed out (firmware refused or hung)"
fi

# --- negatives: refusals must belong to the FIRMWARE ---
# 240 s settle per case: the Access-Denied refusal happens in the
# first ~30 s of BdsDxe; anything still alive at 240 s is the refused
# boot sitting in the boot-manager loop, exactly the negative verdict
# we assert: timeout 124, the boot manager's "Access Denied", and NO
# stub line of any kind -- nothing got past the firmware's own check.
refused_by_firmware() { # $1 esp, $2 log, $3 label; sets FW_REFUSED
    FW_REFUSED=0
    boot_qemu "$1" "$2" 240
    S=$?
    echo "--- $3 serial (key lines) ---"
    grep -a -E "Access Denied|Security Violation|Image is not|stub alive" "$2" || true
    echo "-------------------------------"
    if [[ "$S" -eq 124 ]] \
       && grep -aq "Access Denied" "$2" \
       && ! grep -aq "stub alive" "$2"; then
        FW_REFUSED=1
    elif [[ "$S" -eq 33 ]]; then
        echo "FAIL: $3 BOOTED under enforcement -- enforcement is not real"
    else
        echo "note: $3 did not match the expected refusal shape (status $S, see $2)"
    fi
}

echo "==> negative boot 1/2: the same store, the UNSIGNED stub"
refused_by_firmware "$ESP_UNSIGNED" target/qemu-uefi-ms-secboot-negative.log "the unsigned stub"
NEG_OK=$FW_REFUSED

echo "==> negative boot 2/3: the same store, the SIGNED-BUT-TAMPERED stub"
refused_by_firmware "$ESP_TAMPERED" target/qemu-uefi-ms-secboot-tampered.log "the tampered stub"
TAMPER_OK=$FW_REFUSED

# --- negative 3: the ENROLLED LEAF is revoked BY HASH in dbx ---
# The hash-typed dbx blob (the leaf's TBS, revoked 2020) already lives
# in uefi/src/dbx-neg-signer-hash.der from make_carrier.sh; wrap it in
# the authenticated-update header virt-fw's --set-dbx parses (the
# wrapping is self-checked against virt-fw's OWN parser inside the
# tool), compose the SECOND enforcing store with it, and boot the
# leaf-signed stub. Revocation of a cert that is ALSO in db must bind:
# dbx is checked first, by definition.
REV_STORE=uefi/dist/VARS.ms-secboot-dbxrev.fd
REV_UPDATE=uefi/src/dbx-neg-signer-hash-auth2.der
echo "==> wrapping the leaf TBS-hash dbx blob into the authenticated-update header"
python3 tools/patch_carrier_negatives.py make-dbx-auth-update \
    uefi/src/dbx-neg-signer-hash.der 2020 "$REV_UPDATE"
cp "$VARS_TMPL" "$REV_STORE"
"$VIRT" --inplace "$REV_STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --add-mok "$SHIM" "$LEAF_CERT" \
    --set-dbx "$REV_UPDATE" \
    --sb > /dev/null
echo "==> revoked store shape (dbx must carry the hash-typed revocation):" && \
    "$VIRT" -i "$REV_STORE" -p 2>/dev/null | grep -E "dbx|SecureBoot" || true

echo "==> negative boot 3/3: the REVOKED store, the leaf-signed stub"
REV_OK=0
boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-revoked.log 240 "$REV_STORE"
REV_STATUS=$?
echo "--- revoked-store serial (key lines) ---"
grep -a -E "Access Denied|Security Violation|FAILED|nyrqis ubld|REVOKED" \
    target/qemu-uefi-ms-secboot-revoked.log || true
echo "-----------------------------------"
if [[ "$REV_STATUS" -eq 124 ]] \
   && grep -aq "Access Denied" target/qemu-uefi-ms-secboot-revoked.log \
   && ! grep -aq "nyrqis ubld" target/qemu-uefi-ms-secboot-revoked.log; then
    echo "-- revoked stub refused BY THE FIRMWARE (Access Denied before the stub ever ran)"
    REV_OK=1
elif [[ "$REV_STATUS" -ne 33 ]] \
   && grep -aq "a hash-typed entry covers the signer certificate's To-Be-Signed" \
        target/qemu-uefi-ms-secboot-revoked.log; then
    echo "-- revoked stub refused BY THE STUB'S OWN WALK over the real firmware-served dbx"
    REV_OK=1
elif [[ "$REV_STATUS" -eq 33 ]]; then
    echo "FAIL: the leaf-signed stub BOOTED on the revocation store -- the dbx hash entry did not bind"
else
    echo "note: revoked boot did not match either refusal shape (status $REV_STATUS, see log)"
fi

set -e

if [[ "$POS_OK" -eq 1 && "$NEG_OK" -eq 1 && "$TAMPER_OK" -eq 1 && "$REV_OK" -eq 1 ]]; then
    echo "PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose signer is chained to the enrolled carrier root, refuses both the unsigned one and the one-byte-tampered signed one, refuses the leaf-signed stub once its signer's To-Be-Signed hash is revoked BY HASH in dbx, and the kernel reached Ready"
    exit 0
fi
echo "FAIL: ms OVMF secboot test failed (positive status $POS_STATUS marker_ok=$POS_OK, unsigned refusal_ok=$NEG_OK, tampered refusal_ok=$TAMPER_OK, revocation refusal_ok=$REV_OK)"
exit 1
