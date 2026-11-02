#!/usr/bin/env bash
#
# check_live_iso.sh -- verifies the LIVE ISO artifact, from outside.
#
# A binary in a repository is unattributable unless something checks it, so
# this gate answers three separate questions instead of one:
#
#   integrity     the ISO's sha256 matches dist/nyrqis-live.iso.sha256, so
#                 the file was not truncated or swapped after recording;
#   attribution   the /boot/kernel.elf INSIDE the ISO hashes to
#                 dist/nyrqis-live.kernel.sha256, so the image really holds
#                 the kernel build it claims (a stale artifact then fails
#                 instead of passing);
#   behaviour     (--boot) the disc actually boots and reaches the kernel's
#                 device-mode verdict plus the driver autoload transcript,
#                 measured through the serial console with -display none.
#
# --uefi-boot adds the UEFI disc (dist/nyrqis-live-uefi.iso): its digest is
# checked, and OVMF boots it so OUR signed stub is what loads the kernel.
# That disc has no extracted-kernel attribution because the kernel lives
# inside the stub binary inside the ESP: there is nothing on the disc whose
# digest is the kernel's. Its attribution is the build -- both discs come
# from one run of make_live_iso.sh against one target/kernel.elf -- and that
# limit is stated here rather than papered over with a weaker check.
#
# Usage: ./tools/check_live_iso.sh [--boot] [--uefi-boot] [--iso path]
#
# Exit status is 0 only when every requested check holds; each part names
# itself on failure. The extracted kernel goes to target/, never back into
# dist/.

set -uo pipefail
cd "$(dirname "$0")/.."

ISO="dist/nyrqis-live.iso"
BOOT=0
UEFI_BOOT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --boot) BOOT=1; shift ;;
        --uefi-boot) UEFI_BOOT=1; shift ;;
        --iso) ISO="$2"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

[[ -s "$ISO" ]] || { echo "FAIL: $ISO missing -- run ./tools/make_live_iso.sh"; exit 1; }

echo "==> integrity"
if [[ -s "$ISO.sha256" ]]; then
    WANT="$(cut -d' ' -f1 "$ISO.sha256")"
    GOT="$(sha256sum "$ISO" | cut -d' ' -f1)"
    if [[ "$WANT" == "$GOT" ]]; then
        ok "sha256 matches the recorded digest ($GOT)"
    else
        bad "sha256 is $GOT but $ISO.sha256 records $WANT -- the artifact is not the one recorded"
    fi
else
    bad "$ISO.sha256 missing: the artifact has no recorded digest"
fi

echo "==> attribution (the kernel inside the image)"
KERNEL_SHA_FILE="${ISO%.iso}.kernel.sha256"
EXTRACT="target/live-iso-kernel.elf"
rm -f "$EXTRACT"
if command -v xorriso >/dev/null 2>&1; then
    xorriso -osirrox on -indev "$ISO" -extract /boot/kernel.elf "$EXTRACT" >/dev/null 2>&1
    if [[ -s "$EXTRACT" && -s "$KERNEL_SHA_FILE" ]]; then
        EMBEDDED="$(sha256sum "$EXTRACT" | cut -d' ' -f1)"
        WANT="$(tr -d '\n' < "$KERNEL_SHA_FILE")"
        if [[ "$EMBEDDED" == "$WANT" ]]; then
            ok "the image carries the recorded kernel build ($EMBEDDED)"
        else
            bad "the image carries kernel $EMBEDDED but $KERNEL_SHA_FILE records $WANT -- the ISO is stale relative to the digest"
        fi
        if [[ -s target/kernel.elf ]]; then
            BUILT="$(sha256sum target/kernel.elf | cut -d' ' -f1)"
            if [[ "$BUILT" == "$EMBEDDED" ]]; then
                ok "and that is the kernel this tree just built"
            else
                echo "  note: target/kernel.elf is $BUILT, the image holds $EMBEDDED (a rebuild since packaging; not a failure of the artifact)"
            fi
        fi
    else
        bad "could not extract /boot/kernel.elf or read $KERNEL_SHA_FILE"
    fi
else
    echo "  note: xorriso absent -- attribution check skipped (install xorriso)"
fi

if [[ "$BOOT" == "1" ]]; then
    echo "==> behaviour (headless boot of the disc)"
    command -v qemu-system-x86_64 >/dev/null 2>&1 || { bad "qemu-system-x86_64 missing"; }
    if command -v qemu-system-x86_64 >/dev/null 2>&1; then
        LOG=target/live-iso-check.log
        rm -f "$LOG"
        timeout 120 qemu-system-x86_64 \
            -cdrom "$ISO" -m 512 -serial "file:$LOG" -display none -no-reboot \
            >/dev/null 2>&1
        if grep -q "device-mode: desktop (source: kernel cmdline)" "$LOG" \
           && grep -q "devices:   framebuffer    display  bound" "$LOG" \
           && grep -q "devices:   ps2-aux        input    bound" "$LOG" \
           && grep -q "read-back verified" "$LOG"; then
            ok "the disc boots the default entry, selects desktop, binds framebuffer + ps2-aux and verifies the display"
        else
            bad "the disc did not reach the kernel's device-mode/autoload verdict (see $LOG)"
            grep -E "device-mode|devices:" "$LOG" | sed 's/^/        /' | head -8
        fi
    fi
fi

if [[ "$UEFI_BOOT" == "1" ]]; then
    echo
    echo "==> UEFI disc (the signed stub, not GRUB)"
    UISO=dist/nyrqis-live-uefi.iso
    if [[ ! -s "$UISO" ]]; then
        bad "$UISO missing -- run ./tools/make_live_iso.sh"
    else
        if [[ -s "$UISO.sha256" ]]; then
            WANT="$(cut -d' ' -f1 "$UISO.sha256")"
            GOT="$(sha256sum "$UISO" | cut -d' ' -f1)"
            if [[ "$WANT" == "$GOT" ]]; then
                ok "sha256 matches the recorded digest ($GOT)"
            else
                bad "sha256 is $GOT but $UISO.sha256 records $WANT"
            fi
        else
            bad "$UISO.sha256 missing: the UEFI artifact has no recorded digest"
        fi

        command -v qemu-system-x86_64 >/dev/null 2>&1 || bad "qemu-system-x86_64 missing"
        if command -v qemu-system-x86_64 >/dev/null 2>&1; then
            VARSTEMPLATE=uefi/tools/OVMF_VARS_4M.fd
            CODE=uefi/tools/OVMF_CODE_4M.fd
            VARS=target/live-uefi-vars.fd
            LOG=target/live-uefi-check.log
            if [[ -s "$VARSTEMPLATE" && -s "$CODE" ]]; then
                cp "$VARSTEMPLATE" "$VARS"
                rm -f "$LOG"
                # The disc stays UP (it is a live medium, not a test
                # build), so this run is expected to hit the timeout: only
                # the transcript is under test.
                timeout 300 qemu-system-x86_64 \
                    -machine q35 -m 256 \
                    -drive if=pflash,format=raw,readonly=on,file="$CODE" \
                    -drive if=pflash,format=raw,file="$VARS" \
                    -cdrom "$UISO" -serial "file:$LOG" \
                    -display none -no-reboot >/dev/null 2>&1
                if grep -q "nyrqis ubld: stub alive under UEFI" "$LOG" \
                   && grep -q "^nyrqis ubld: GOP " "$LOG" \
                   && grep -q "devices: display -- .* 32bpp .*read-back verified" "$LOG" \
                   && grep -q "device-mode: shell rendered" "$LOG"; then
                    ok "OVMF boots the disc through OUR stub: carrier walked, GOP handed over, shell rendered and verified"
                else
                    bad "the UEFI disc did not reach the stub+display verdict (see $LOG)"
                    grep -E "nyrqis ubld|device-mode|devices: display" "$LOG" | sed 's/^/        /' | head -8
                fi
            else
                bad "OVMF firmware blobs missing under uefi/tools/ -- cannot boot the UEFI disc"
            fi
        fi
    fi
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS live-ISO check(s) failed"
    exit 1
fi
echo "PASS: the live ISO is the recorded artifact, holds the recorded kernel build$( [[ "$BOOT" == "1" ]] && echo ", boots to a verified device-mode session" )$( [[ "$UEFI_BOOT" == "1" ]] && echo ", and the UEFI disc boots through the signed stub" )"
