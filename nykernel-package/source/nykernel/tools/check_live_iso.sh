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
# receipt       a boot verdict is evidence about ONE kernel build, and prose
#               in a log does not survive a rebuild: tomorrow's disc inherits
#               yesterday's "it booted". So a run that asks for a boot leg
#               also mints dist/nyrqis-live.boot-receipt.json, naming -- per
#               leg -- the sha256 of the kernel that leg actually booted
#               beside that leg's verdict: for the BIOS disc the digest of the
#               /boot/kernel.elf extracted from the disc (the file GRUB loads),
#               and for the UEFI disc the stub's own "kernel image sha256
#               computed" line, i.e. the digest it took over the bytes it
#               loaded (it dies rather than load a kernel that disagrees with
#               the build's recorded digest). Every run of this script then
#               compares the receipt with the artefacts on disk, and a receipt
#               that names a different kernel or a different ISO fails the
#               check: no rebuild can silently inherit an old boot claim, it
#               has to earn a new one by booting again. A receipt records the
#               legs THAT run measured, so the full claim is the both-flag run.
#
# The OTHER half of that contract -- that a disc which cannot boot drives this
# gate red and mints FAIL instead of inheriting the PASS already on disk -- is
# proved on the real artefacts by tools/live_iso_selfcheck.sh, which gives the
# UEFI disc an unloadable BOOTX64.EFI, requires the red verdict and the FAIL
# receipt, then restores the discs and re-earns the PASS.
#
# Usage: ./tools/check_live_iso.sh [--boot] [--uefi-boot] [--iso path]
#
# Exit status is 0 only when every requested check holds; each part names
# itself on failure. The extracted kernel goes to target/, never back into
# dist/.

set -uo pipefail
cd "$(dirname "$0")/.."

# See tools/win_toolchain.sh: on Windows this supplies QEMU, xorriso and the
# ELF/PE binutils dispatchers; on Linux it is not reached.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
    # shellcheck disable=SC1091
    source tools/win_toolchain.sh || exit 1
fi

ISO="dist/nyrqis-live.iso"
BOOT=0
UEFI_BOOT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --boot) BOOT=1; shift ;;
        --uefi-boot) UEFI_BOOT=1; shift ;;
        --iso) ISO="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# The receipt and the build digest it has to agree with. A leg is recorded as
# "<verdict>:<iso sha256>:<kernel the boot actually loaded>" and only once that
# leg has been measured in THIS run -- a leg nobody booted is left out rather
# than copied forward from an older receipt, which is the whole point.
RECEIPT="dist/nyrqis-live.boot-receipt.json"
KERNEL_SHA_FILE="dist/nyrqis-live.kernel.sha256"
KERNEL_BUILD_SHA=""
[[ -s "$KERNEL_SHA_FILE" ]] && KERNEL_BUILD_SHA="$(tr -d '\n\r' < "$KERNEL_SHA_FILE")"
LEG_BIOS=""
LEG_UEFI=""

# One field out of the receipt, addressed as a dotted path. Exit 1 when the
# receipt, the path or the value is absent -- callers treat that as "no claim".
receipt_field() {
    python3 - "$RECEIPT" "$1" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
cur = doc
for key in sys.argv[2].split("."):
    if not isinstance(cur, dict) or key not in cur:
        sys.exit(1)
    cur = cur[key]
if cur is None:
    sys.exit(1)
print("true" if cur is True else "false" if cur is False else cur)
PY
}

# Write the receipt from this run's measurements. Kept in python because the
# file is read by machines and a half-written JSON document must never be the
# thing a later run diffs: the write goes to a temporary beside it and is
# renamed into place.
mint_receipt() {
    RECEIPT_PATH="$RECEIPT" REC_VERDICT="$1" \
    REC_KERNEL="$KERNEL_BUILD_SHA" REC_BIOS="$LEG_BIOS" REC_UEFI="$LEG_UEFI" \
    python3 - <<'PY'
import json, os, time

def leg(spec, iso, source):
    if not spec:
        return None
    verdict, iso_sha, booted_kernel = spec.split(":", 2)
    build = os.environ.get("REC_KERNEL") or None
    return {
        "iso": iso,
        "iso_sha256": iso_sha or None,
        "verdict": verdict,
        "booted_kernel_sha256": booted_kernel or None,
        "booted_kernel_source": source,
        "matches_build_digest": bool(booted_kernel and build and booted_kernel == build),
    }

doc = {
    "gate": "check_live_iso.sh",
    "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "verdict": os.environ["REC_VERDICT"],
    "kernel_sha256": os.environ.get("REC_KERNEL") or None,
    "kernel_sha256_source": "dist/nyrqis-live.kernel.sha256, the build both discs come from",
    "legs": {},
}
bios = leg(os.environ.get("REC_BIOS", ""), "dist/nyrqis-live.iso",
           "/boot/kernel.elf inside the disc -- the file GRUB loaded")
uefi = leg(os.environ.get("REC_UEFI", ""), "dist/nyrqis-live-uefi.iso",
           "the stub's sha256 of the kernel bytes it loaded (its 'kernel image sha256 computed' line)")
if bios:
    doc["legs"]["bios"] = bios
if uefi:
    doc["legs"]["uefi"] = uefi
path = os.environ["RECEIPT_PATH"]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as out:
    json.dump(doc, out, indent=2, sort_keys=True)
    out.write("\n")
os.replace(tmp, path)
print(f"receipt: {path}")
for name in sorted(doc["legs"]):
    entry = doc["legs"][name]
    print(f"         {name}: {entry['verdict']}, booted kernel {entry['booted_kernel_sha256'] or 'unrecorded'}")
print(f"         kernel this claim covers: {doc['kernel_sha256'] or 'unrecorded'}")
PY
}

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
    LEG_FAILS=$FAILS
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
        # The boot verdict is evidence about a kernel, so name it: the digest
        # of the /boot/kernel.elf taken out of the image that was just booted.
        # Whatever the tree built elsewhere is irrelevant here.
        if [[ -z "${EMBEDDED:-}" ]]; then
            bad "the BIOS boot cannot be tied to a kernel digest (no extracted /boot/kernel.elf -- is xorriso installed?)"
        elif [[ -n "$KERNEL_BUILD_SHA" && "$EMBEDDED" != "$KERNEL_BUILD_SHA" ]]; then
            bad "the disc booted kernel $EMBEDDED but $KERNEL_SHA_FILE records $KERNEL_BUILD_SHA -- the image is not this build"
        else
            ok "and the kernel it booted is $EMBEDDED -- the build the tree records"
        fi
        LEG_BIOS="$( [[ $FAILS -eq $LEG_FAILS ]] && echo PASS || echo FAIL ):$(sha256sum "$ISO" | cut -d' ' -f1):${EMBEDDED:-}"
    fi
fi

if [[ "$UEFI_BOOT" == "1" ]]; then
    echo
    echo "==> UEFI disc (the signed stub, not GRUB)"
    LEG_FAILS=$FAILS
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
                # There is no kernel file to extract here, so the tie comes
                # from the stub itself: it hashes the kernel bytes it loads and
                # refuses to load them when they disagree with the build-time
                # digest, so its printed digest IS the kernel this boot ran.
                UBLD_KERNEL="$(tr -d '\r' < "$LOG" | sed -n 's/^nyrqis ubld: kernel image sha256 computed = \([0-9a-f]\{64\}\)$/\1/p' | tail -1)"
                if [[ -z "$UBLD_KERNEL" ]]; then
                    bad "the stub's transcript names no kernel digest -- the UEFI boot cannot be tied to a build"
                elif [[ -n "$KERNEL_BUILD_SHA" && "$UBLD_KERNEL" != "$KERNEL_BUILD_SHA" ]]; then
                    bad "the stub booted kernel $UBLD_KERNEL but $KERNEL_SHA_FILE records $KERNEL_BUILD_SHA -- the disc is not this build"
                else
                    ok "and the kernel it loaded is $UBLD_KERNEL -- the build the tree records"
                fi
                LEG_UEFI="$( [[ $FAILS -eq $LEG_FAILS ]] && echo PASS || echo FAIL ):$(sha256sum "$UISO" | cut -d' ' -f1):$UBLD_KERNEL"
            else
                bad "OVMF firmware blobs missing under uefi/tools/ -- cannot boot the UEFI disc"
            fi
        fi
    fi
fi

# ---- the receipt: name the kernel, or keep no claim ----------------------
# A boot run replaces the receipt outright. Deleting first is deliberate: if
# minting then fails, the tree is left with NO claim rather than yesterday's
# PASS standing in for a boot nobody did.
if [[ "$BOOT" == "1" || "$UEFI_BOOT" == "1" ]]; then
    echo
    echo "==> boot receipt (a boot verdict is evidence about one kernel)"
    rm -f "$RECEIPT"
    if [[ "$FAILS" -eq 0 ]]; then
        mint_receipt PASS || bad "could not write $RECEIPT"
    else
        mint_receipt FAIL || bad "could not write $RECEIPT"
    fi
fi

# ---- and does that claim cover the artefacts that are on disk now? -------
# This runs on EVERY invocation, boot leg or not: after a rebuild the receipt
# still describes the previous kernel, and saying so is the difference between
# "this tree booted" and "some earlier tree booted".
echo
echo "==> boot claim (does the recorded receipt cover this build?)"
if [[ ! -s "$RECEIPT" ]]; then
    echo "  note: no boot receipt at $RECEIPT -- this run claims no boot (run --boot --uefi-boot to earn one)"
else
    CLAIM_VERDICT="$(receipt_field verdict || true)"
    CLAIM_KERNEL="$(receipt_field kernel_sha256 || true)"
    STALE=0
    if [[ -n "$KERNEL_BUILD_SHA" && -n "$CLAIM_KERNEL" && "$KERNEL_BUILD_SHA" != "$CLAIM_KERNEL" ]]; then
        bad "the receipt covers kernel $CLAIM_KERNEL but this tree records $KERNEL_BUILD_SHA -- the old boot claim does not cover this build"
        STALE=1
    fi
    LEGS_CLAIMED=""
    for leg in bios uefi; do
        RECORDED_ISO_SHA="$(receipt_field "legs.$leg.iso_sha256" || true)"
        [[ -n "$RECORDED_ISO_SHA" ]] || continue
        LEGS_CLAIMED="${LEGS_CLAIMED:+$LEGS_CLAIMED+}$leg"
        LEG_ISO="dist/nyrqis-live.iso"
        [[ "$leg" == "uefi" ]] && LEG_ISO="dist/nyrqis-live-uefi.iso"
        if [[ ! -s "$LEG_ISO" ]]; then
            bad "the receipt records a $leg boot but $LEG_ISO is gone -- the claim has no artefact behind it"
            STALE=1
            continue
        fi
        NOW_SHA="$(sha256sum "$LEG_ISO" | cut -d' ' -f1)"
        if [[ "$NOW_SHA" != "$RECORDED_ISO_SHA" ]]; then
            bad "the receipt's $leg boot covered ISO $RECORDED_ISO_SHA but $LEG_ISO is now $NOW_SHA -- a rebuild cannot inherit the old boot claim"
            STALE=1
        fi
        RECORDED_KERNEL="$(receipt_field "legs.$leg.booted_kernel_sha256" || true)"
        if [[ -z "$RECORDED_KERNEL" ]]; then
            bad "the receipt's $leg leg names no booted kernel -- its verdict cannot be tied to a build"
            STALE=1
        elif [[ -n "$KERNEL_BUILD_SHA" && "$RECORDED_KERNEL" != "$KERNEL_BUILD_SHA" ]]; then
            bad "the receipt's $leg boot loaded kernel $RECORDED_KERNEL but this tree records $KERNEL_BUILD_SHA -- stale claim"
            STALE=1
        fi
    done
    if [[ "$STALE" -eq 0 ]]; then
        if [[ "$CLAIM_VERDICT" == "PASS" ]]; then
            ok "the receipt covers this build: kernel ${CLAIM_KERNEL:-unrecorded}, leg(s) ${LEGS_CLAIMED:-none} -- a rebuild must earn a new claim (rerun --boot --uefi-boot)"
        else
            echo "  note: the receipt covers this build but records verdict ${CLAIM_VERDICT:-UNKNOWN} -- this build has no PASS boot evidence"
        fi
    fi
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS live-ISO check(s) failed"
    exit 1
fi
echo "PASS: the live ISO is the recorded artifact, holds the recorded kernel build$( [[ "$BOOT" == "1" ]] && echo ", boots to a verified device-mode session" )$( [[ "$UEFI_BOOT" == "1" ]] && echo ", and the UEFI disc boots through the signed stub" )$( [[ -s "$RECEIPT" ]] && echo ", with $RECEIPT naming the kernel it booted" )"
