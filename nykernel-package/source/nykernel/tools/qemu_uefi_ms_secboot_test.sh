#!/usr/bin/env bash
#
# qemu_uefi_ms_secboot_test.sh -- the secure-boot-enforcing twin of
# qemu_uefi_boot_test.sh. Same kernel+stub build, but the firmware is
# the enforcing OVMF (-4M.ms) image and the variable store is FRESHLY
# enrolled (from the shipped .ms template) with the carrier chain
# itself: the ADR-0014 root pair as PK/KEK, root+leaf certificates in
# db, SecureBootEnable on. The stub is then Authenticode-signed with
# the leaf key (sbsign) and installed on the ESP, so the FIRMWARE's
# verification -- not ours -- is the gate the stub passes, and the
# stub's own carrier walk (which anchors the same root) still enforces
# the kernel image inside it. Two boots assert both directions:
#
# positive: signed stub under enforcement -> firmware accepts it, the
#   stub reads SecureBoot=1 with PK present (not SetupMode), the
#   carrier chain verifies over the kernel, and the boot reaches Ready
#   (host exit code 33, the same contract as the non-ms gate).
# negative: the UNSIGNED stub against the same enforcing store -> the
#   firmware refuses it at the boot manager ("Access Denied") and the
#   boot never reaches the stub (host timeout catches the idle loop).
#
# This is what CI should run to prove the ms OVMF path, the same way
# qemu_uefi_boot_test.sh proves the default OVMF path.

set -euo pipefail
cd "$(dirname "$0")/.."

FAKECODE=uefi/tools/OVMF_CODE_4M.ms.fd
FAKEVARS_TMPL=uefi/tools/OVMF_VARS_4M.ms.fd
for f in "$FAKECODE" "$FAKEVARS_TMPL"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: ms OVMF image missing: $f"
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

for tool in sbsign sbverify mformat mmd mcopy qemu-system-x86_64; do
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

# --- compose the enforcing store from the shipped ms template ---
# The template itself carries the Microsoft PK/KEK with
# SecureBootEnable ON, which refuses our unsigned images no matter
# what our db says; enrollment REPLACES that authority with the
# carrier chain's own (dev-only, bring-up): root as PK and KEK, both
# certificates in db. Ownership GUIDs are the standard ones.
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
STORE=uefi/dist/VARS.ms-secboot.fd
cp "$FAKEVARS_TMPL" "$STORE"
# NOTE: --inplace (or -o) is REQUIRED to write back; virt-fw-vars with
# -i alone only PLANS the changes, the store on disk keeps the
# template's Microsoft keys and the firmware then refuses even our
# correctly-signed stub.
"$VIRT" --inplace "$STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --sb > /dev/null
echo "==> enrolled enforcing store: PK/KEK anchored on the carrier root, root+leaf in db, SecureBootEnable ON"

# --- Authenticode-sign the stub with the leaf key ---
# The signer certificate must be one the firmware's db can bind
# directly: the leaf is enrolled verbatim, so AuthenticodeVerify
# matches the signer on sight.
SIGNED_STUB=uefi/dist/nyrqis_uefi_stub-signed.efi
sbsign --key "$LEAF_KEY" --cert "$LEAF_CERT" "$STUB_EFI" --output "$SIGNED_STUB" > /dev/null
sbverify --list "$SIGNED_STUB" | head -4

# --- two ESP images: one with the signed stub, one with the raw one ---
mk_esp() {
    rm -f "$1"
    mkdir -p uefi/dist/EFI/BOOT
    mformat -i "$1" -C -T 131072
    mmd -i "$1" ::EFI ::EFI/BOOT
    mcopy -i "$1" "$2" ::EFI/BOOT/BOOTX64.EFI
}
ESP_SIGNED=uefi/dist/esp-signed.img
ESP_UNSIGNED=uefi/dist/esp-unsigned.img
mk_esp "$ESP_SIGNED" "$SIGNED_STUB"
mk_esp "$ESP_UNSIGNED" "$STUB_EFI"

boot_qemu() { # $1 esp image, $2 serial log, $3 timeout seconds
    timeout "$3" qemu-system-x86_64 \
        -machine q35 \
        -m 256 \
        -drive if=pflash,format=raw,readonly=on,file="$FAKECODE" \
        -drive if=pflash,format=raw,file="$STORE" \
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
       && grep -aq "boot phase = 7 (Ready)" target/qemu-uefi-ms-secboot-positive.log; then
        POS_OK=1
    else
        echo "note: boot reached Ready but a required secboot marker line is missing (see log)"
    fi
elif [[ "$POS_STATUS" -eq 124 ]]; then
    echo "FAIL: positive boot timed out (firmware refused or hung)"
fi

# --- negative: the unsigned stub must be refused BY THE FIRMWARE ---
# 240 s settle: the Access-Denied refusal happens in the first ~30 s
# of BdsDxe; anything still alive at 240 s is the refused boot sitting
# in the boot-manager loop, exactly the negative verdict we assert.
echo "==> negative boot: the same store, the UNSIGNED stub"
boot_qemu "$ESP_UNSIGNED" target/qemu-uefi-ms-secboot-negative.log 240
NEG_STATUS=$?
echo "--- negative serial (key lines) ---"
grep -a -E "Access Denied|Security Violation|Image is not|stub alive" \
    target/qemu-uefi-ms-secboot-negative.log || true
echo "-----------------------------------"
# No stub line may appear at all: the refusal must belong to the
# FIRMWARE (DB idle at the boot manager), not to anything that got
# past it. The idle-refused VM is the 480 s festival (timeout 124).
NEG_OK=0
if [[ "$NEG_STATUS" -eq 124 ]] \
   && grep -aq "Access Denied" target/qemu-uefi-ms-secboot-negative.log \
   && ! grep -aq "stub alive" target/qemu-uefi-ms-secboot-negative.log; then
    NEG_OK=1
elif [[ "$NEG_STATUS" -eq 33 ]]; then
    echo "FAIL: the unsigned stub BOOTED under enforcement -- enforcement is not real"
fi

set -e

if [[ "$POS_OK" -eq 1 && "$NEG_OK" -eq 1 ]]; then
    echo "PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose signer is chained to the enrolled carrier root, refuses the unsigned one, and the kernel reached Ready"
    exit 0
fi
echo "FAIL: ms OVMF secboot test failed (positive status $POS_STATUS marker_ok=$POS_OK, negative status $NEG_STATUS marker_ok=$NEG_OK)"
exit 1
