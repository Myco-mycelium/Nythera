#!/usr/bin/env bash
#
# live_iso_selfcheck.sh -- prove tools/check_live_iso.sh's FAILING-BOOT path
# on the REAL discs, and that a failed boot mints a FAIL receipt instead of
# inheriting the PASS that was already on disk.
#
#   ./tools/live_iso_selfcheck.sh
#
# The gate's boot legs are claims of the form "this disc boots, or I say so".
# Until this tool existed, the only evidence for the second half was the
# inherited-PASS direction (a rebuild making the old claim stale, which is a
# DIFFERENT question), so nothing had ever seen the gate fail a disc that was
# internally consistent and simply could not boot its second leg. That is the
# shape of the bug this repository keeps meeting: a receipt saying PASS about a
# boot nobody did, because the verdict was carried forward rather than measured.
#
# What it does, in order:
#
#   1. requires a PASS receipt that actually covers the discs and the kernel
#      build on disk. That receipt is the thing the mutation must NOT inherit;
#      without one the run would prove nothing, so it refuses and names the
#      command that earns one.
#   2. backs up both discs, both recorded digests and the receipt, and restores
#      them from an EXIT trap as well as on the normal path, so an interrupt
#      cannot leave a mutant disc or a FAIL receipt in the tree.
#   3. mutates ONE leg: it repacks dist/nyrqis-live-uefi.iso with the same
#      El Torito EFI layout but an ESP whose ::EFI/BOOT/BOOTX64.EFI is random
#      bytes, then re-records that disc's own digest. The disc therefore
#      FORMATS, HAS the right layout and MATCHES its recorded sha256 -- the
#      only thing wrong with it is that it cannot boot, which is the point:
#      a mutation that broke the digest would be caught by the integrity check
#      and would prove nothing about the boot legs. The firmware does not find
#      a loadable image, so OVMF lands in its interactive shell.
#   4. runs ./tools/check_live_iso.sh --boot --uefi-boot and requires ALL of:
#      a non-zero exit, the UEFI boot verdict named as the failure, the BIOS
#      leg still green (the gate blamed the right leg, not everything), the
#      mutant disc still passing its own integrity line, and a receipt on disk
#      that reads verdict FAIL with the UEFI leg FAIL and NO booted kernel --
#      i.e. a refusal, not the previous PASS wearing a new timestamp.
#   5. restores the discs byte-for-byte (verified with cmp, then sha256sum -c),
#      re-runs the gate, and requires a PASS receipt naming both legs and the
#      kernel this tree records.
#   6. leaves the tree as it found it: if the freshly minted receipt is the
#      same claim as the one on record (same kernel, same leg digests, same
#      verdicts -- only "generated" differs), the recorded bytes are put back,
#      because a timestamp is not evidence and a dirty tree is a worse
#      artifact than an identical receipt. If the claim genuinely CHANGED, the
#      new receipt is kept and this check FAILS, saying it has to be committed:
#      the one outcome it must never produce is "PASS" over a modified dist/.
#
# Cost: two real boot runs, so roughly a quarter of an hour (the BIOS leg is
# bounded at 120 s and the UEFI leg at 300 s, and the mutant rides that
# timeout). That is the price of not editing a transcript.
#
# Requires the toolchain the gate itself needs -- qemu-system-x86_64, xorriso,
# mformat/mcopy/mmd and the OVMF blobs -- on PATH; ./tools/install_boot_tools.sh
# installs the first five without root. Intermediates go to
# target/live-selfcheck/ (gitignored), never into dist/.
#
# Exit: 0 the mutant was caught for the right reason and the restored discs
# re-earned a PASS with the tree clean; 1 otherwise.

set -uo pipefail
cd "$(dirname "$0")/.."

BIOS=dist/nyrqis-live.iso
BIOS_SHA="$BIOS.sha256"
UEFI=dist/nyrqis-live-uefi.iso
UEFI_SHA="$UEFI.sha256"
KERNEL_SHA_FILE=dist/nyrqis-live.kernel.sha256
RECEIPT=dist/nyrqis-live.boot-receipt.json
GATE=./tools/check_live_iso.sh
WORK=target/live-selfcheck

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }
# A precondition that did not hold is not a check that failed: it means this
# run could not test what it claims to, so it stops instead of reporting a
# verdict it did not measure.
refuse() { echo "FAIL: $*"; exit 1; }

disc_sha() { sha256sum "$1" | cut -d' ' -f1; }

# One field out of a receipt, addressed as a dotted path, from an explicit
# file (unlike the gate's helper, which always reads the one on disk): this
# tool compares the receipt it found with the one a run minted. Exit 1 when
# the file, the path or the value is absent.
receipt_field() {
    python3 - "$1" "$2" <<'PY'
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

# Are two receipts the SAME CLAIM? Everything that is evidence is compared;
# "generated" is not evidence, it is when the sentence was written. This is
# what decides whether the re-earned receipt may be replaced by the recorded
# bytes (tree stays clean) or has to be kept and committed.
same_claim() {
    python3 - "$1" "$2" <<'PY'
import json, sys

def claim(path):
    doc = json.load(open(path, encoding="utf-8"))
    doc.pop("generated", None)
    return doc

try:
    a, b = claim(sys.argv[1]), claim(sys.argv[2])
except Exception:
    sys.exit(1)
sys.exit(0 if a == b else 1)
PY
}

# The mutant tree, its ESP and the packed disc are all under target/, so a
# failed mutation cannot touch dist/ until the backup above exists.
TMP="$(mktemp -d)"
MUTATED=0
KEEP_RECEIPT=0
# Restoration is not left to the happy path. An interrupt, or any early exit
# below, must put the real discs and the real receipt back -- a self-check that
# can leave a sabotaged disc in dist/ is worse than no self-check.
restore_tree() {
    if [[ "$MUTATED" -eq 1 ]]; then
        cp "$TMP/uefi.iso" "$UEFI" 2>/dev/null || true
        cp "$TMP/uefi.iso.sha256" "$UEFI_SHA" 2>/dev/null || true
        MUTATED=0
        echo "  (restored $UEFI from its backup on exit)"
    fi
    if [[ "$KEEP_RECEIPT" -eq 0 && -f "$TMP/receipt.json" ]]; then
        cp "$TMP/receipt.json" "$RECEIPT" 2>/dev/null || true
    fi
    rm -rf "$TMP"
}
trap restore_tree EXIT
trap 'echo "  (interrupted -- discs and receipt restored from backup)"; exit 130' INT TERM

# ---- preconditions: say which assumption is broken ------------------------
echo "==> preconditions"
for tool in qemu-system-x86_64 xorriso mformat mmd mcopy git; do
    command -v "$tool" >/dev/null 2>&1 \
        || refuse "$tool is not on PATH -- run ./tools/install_boot_tools.sh and put its bin directory on PATH"
done
[[ -x "$GATE" ]] || refuse "$GATE is not executable"
for f in "$BIOS" "$BIOS_SHA" "$UEFI" "$UEFI_SHA" "$KERNEL_SHA_FILE" "$RECEIPT"; do
    [[ -s "$f" ]] || refuse "$f is missing or empty -- run ./tools/make_live_iso.sh, then $GATE --boot --uefi-boot"
done
for f in uefi/tools/OVMF_CODE_4M.fd uefi/tools/OVMF_VARS_4M.fd; do
    [[ -s "$f" ]] || refuse "$f is missing: the UEFI leg cannot boot, so a red gate would prove nothing"
done
# The mutation is undone by copying the backup back; a dist/ that was already
# modified could never end up clean, and this tool promises a clean tree.
DIRTY="$(git status --porcelain -- dist)"
[[ -z "$DIRTY" ]] || refuse "dist/ has uncommitted changes, so a restored tree could not be called clean:
$(printf '%s\n' "$DIRTY" | sed 's/^/        /')
        commit or check out those paths first"

# ---- the claim the mutation must NOT inherit ------------------------------
# A red gate proves little if there was no PASS to inherit: the interesting
# failure is a run that could have carried the old verdict forward and did
# not. So the receipt has to be a PASS that covers exactly the discs and the
# kernel build on disk, checked field by field BEFORE anything is mutated.
echo "==> the receipt this mutation must not inherit"
BUILD_SHA="$(tr -d '\n\r' < "$KERNEL_SHA_FILE")"
have_claim=1
[[ "$(receipt_field "$RECEIPT" verdict || true)" == "PASS" ]] || have_claim=0
[[ "$(receipt_field "$RECEIPT" kernel_sha256 || true)" == "$BUILD_SHA" ]] || have_claim=0
[[ "$(receipt_field "$RECEIPT" legs.bios.iso_sha256 || true)" == "$(disc_sha "$BIOS")" ]] || have_claim=0
[[ "$(receipt_field "$RECEIPT" legs.uefi.iso_sha256 || true)" == "$(disc_sha "$UEFI")" ]] || have_claim=0
[[ "$(receipt_field "$RECEIPT" legs.uefi.booted_kernel_sha256 || true)" == "$BUILD_SHA" ]] || have_claim=0
if [[ "$have_claim" -eq 0 ]]; then
    refuse "$RECEIPT is not a PASS covering the discs and kernel on disk -- there is no verdict for a failed boot to inherit, so this run would prove nothing.
        earn one first: $GATE --boot --uefi-boot"
fi
ok "a PASS receipt covers these discs and kernel $BUILD_SHA -- that is the claim a failed boot must replace, not reuse"

# ---- backup, then mutate ONE leg -----------------------------------------
cp "$BIOS" "$TMP/bios.iso" || refuse "could not back up $BIOS"
cp "$UEFI" "$TMP/uefi.iso" || refuse "could not back up $UEFI"
cp "$UEFI_SHA" "$TMP/uefi.iso.sha256" || refuse "could not back up $UEFI_SHA"
cp "$RECEIPT" "$TMP/receipt.json" || refuse "could not back up $RECEIPT"
GOOD_UEFI_SHA="$(disc_sha "$UEFI")"

# The El Torito layout this mutation must reproduce: same EFI image path,
# same platform, bootable, non-emulated. The size and LBA are expected to
# differ (the mutant ESP is smaller than the real stub's), so they are left
# out -- everything else is compared against the real disc below, which is how
# a drifted repack gets caught instead of quietly proving nothing.
el_torito_shape() {
    xorriso -indev "$1" -report_el_torito plain 2>/dev/null \
        | sed -n -e 's/^El Torito boot img :   1  \([A-Za-z]*\)  \([yn]\)  \([a-z]*\).*/img \1 \2 \3/p' \
                 -e 's/^El Torito img path :   1  \(.*\)$/path \1/p'
}

echo "==> mutation: the UEFI disc gets a BOOTX64.EFI the firmware cannot load"
rm -rf "$WORK"
mkdir -p "$WORK/tree/boot" || refuse "could not create $WORK/tree/boot"
ESP="$WORK/efi.img"
STUB="$WORK/BOOTX64.EFI"
# 4 MiB of FAT is enough for one file and matches the ESP's own floor
# (make_live_iso.sh never goes below it); the stub here is 1 MiB of random
# bytes rather than a real PE, so the firmware finds an image it refuses to
# execute and falls through to its shell.
mformat -i "$ESP" -C -T $(( 4 * 1024 * 1024 / 512 )) >/dev/null 2>&1 \
    || refuse "mformat could not create $ESP"
mmd -i "$ESP" ::EFI ::EFI/BOOT >/dev/null 2>&1 || refuse "mmd could not create ::EFI/BOOT"
head -c 1048576 /dev/urandom > "$STUB" || refuse "could not write a random stub"
mcopy -i "$ESP" "$STUB" ::EFI/BOOT/BOOTX64.EFI >/dev/null 2>&1 \
    || refuse "mcopy could not place BOOTX64.EFI on the ESP"
cp "$ESP" "$WORK/tree/boot/efi.img" || refuse "could not stage the mutant ESP"
# Same flags as tools/make_live_iso.sh's UEFI packaging. The layout guard
# below is what keeps this copy honest if that invocation ever changes.
xorriso -as mkisofs -o "$UEFI" \
    -isohybrid-gpt-basdat -eltorito-alt-boot \
    -e boot/efi.img -no-emul-boot -V NYRQIS_UEFI \
    "$WORK/tree" >/dev/null 2>&1
[[ -s "$UEFI" ]] || refuse "xorriso produced no mutant disc"
MUTATED=1
MUTANT_SHA="$(disc_sha "$UEFI")"
# Re-record the mutant's OWN digest, exactly as the build does. This is the
# line that keeps the mutation about the boot: the gate's integrity check has
# nothing to complain about, so a red verdict can only come from the boot leg.
printf '%s  %s\n' "$MUTANT_SHA" nyrqis-live-uefi.iso > "$UEFI_SHA"

if cmp -s "$UEFI" "$TMP/uefi.iso"; then
    bad "the mutation changed nothing: $UEFI is byte-identical to the real disc"
else
    ok "rebuilt $UEFI with an unbootable BOOTX64.EFI ($MUTANT_SHA)"
fi
if [[ "$(disc_sha "$BIOS")" != "$(disc_sha "$TMP/bios.iso")" ]]; then
    bad "the BIOS disc changed during the mutation -- one leg was supposed to be touched, not both"
else
    ok "the BIOS disc is untouched ($(disc_sha "$BIOS"))"
fi
GOOD_SHAPE="$(el_torito_shape "$TMP/uefi.iso")"
MUTANT_SHAPE="$(el_torito_shape "$UEFI")"
if [[ -z "$GOOD_SHAPE" || "$GOOD_SHAPE" != "$MUTANT_SHAPE" ]]; then
    bad "the mutant disc's El Torito layout differs from the real one, so a failure would not be attributable to the stub:
        real:   $(printf '%s' "$GOOD_SHAPE" | tr '\n' ' ')
        mutant: $(printf '%s' "$MUTANT_SHAPE" | tr '\n' ' ')"
else
    ok "the mutant reproduces the real disc's El Torito layout ($(printf '%s' "$MUTANT_SHAPE" | tr '\n' ' '))"
fi
if ( cd dist && sha256sum -c nyrqis-live-uefi.iso.sha256 >/dev/null 2>&1 ); then
    ok "and it matches the digest recorded for it -- the gate cannot blame integrity"
else
    bad "the mutant disc does not match its own recorded digest: the mutation is not self-consistent"
fi

# ---- the failing boot ----------------------------------------------------
echo
echo "==> run 1: the gate must go RED, and mint FAIL rather than inherit PASS"
LOG1="$WORK/check-live-mutant.log"
"$GATE" --boot --uefi-boot > "$LOG1" 2>&1
S1=$?
if [[ "$S1" -eq 0 ]]; then
    bad "the gate PASSED an unbootable UEFI disc -- its UEFI leg does not depend on the disc booting"
    tail -12 "$LOG1" | sed 's/^/        /'
else
    ok "the gate exited $S1 on a disc whose stub cannot be loaded"
fi
# Name the failure, not just its existence: a red gate for some other reason
# (a missing tool, a broken log path) would satisfy "non-zero" and prove
# nothing about the boot verdict.
if grep -qF "the UEFI disc did not reach the stub+display verdict" "$LOG1"; then
    ok "it named the UEFI boot verdict as the failure"
else
    bad "the gate failed without naming the UEFI boot verdict"
    grep -E "^  FAIL" "$LOG1" | head -4 | sed 's/^/        /'
fi
if grep -qF "the stub's transcript names no kernel digest" "$LOG1"; then
    ok "and said the boot cannot be tied to a kernel -- no booted digest to record"
else
    bad "the gate did not say the failed boot names no kernel digest"
fi
if grep -qF "sha256 matches the recorded digest ($MUTANT_SHA)" "$LOG1"; then
    ok "the mutant disc still passed its own integrity line -- the red verdict is about booting"
else
    bad "the mutant disc was not accepted by the integrity check, so this run did not test the boot leg"
fi
if grep -qF "the disc boots the default entry, selects desktop" "$LOG1"; then
    ok "the BIOS leg stayed green: the gate blamed one leg, not both"
else
    bad "the BIOS leg did not pass, so this run cannot show the failure was confined to the UEFI disc"
fi

# The receipt is the artifact this whole tool exists for: after a failed boot
# it must say FAIL and name no booted kernel. An inherited PASS is exactly the
# bug, so the old verdict, the old kernel and the old disc digest are all
# checked against -- any one of them surviving would be the defect.
echo "==> the receipt minted by the failed boot"
if [[ ! -s "$RECEIPT" ]]; then
    bad "the failed run minted no receipt at all -- a later run could still inherit the old PASS"
else
    NEW_VERDICT="$(receipt_field "$RECEIPT" verdict || true)"
    NEW_UEFI_VERDICT="$(receipt_field "$RECEIPT" legs.uefi.verdict || true)"
    NEW_UEFI_KERNEL="$(receipt_field "$RECEIPT" legs.uefi.booted_kernel_sha256 || true)"
    NEW_UEFI_ISO="$(receipt_field "$RECEIPT" legs.uefi.iso_sha256 || true)"
    NEW_BIOS_VERDICT="$(receipt_field "$RECEIPT" legs.bios.verdict || true)"
    cp "$RECEIPT" "$WORK/receipt-fail.json"
    if [[ "$NEW_VERDICT" == "FAIL" ]]; then
        ok "the receipt reads verdict FAIL -- the old PASS was replaced, not inherited"
    else
        bad "the receipt still reads verdict '${NEW_VERDICT:-unreadable}' after a failed boot: the gate inherited the old claim"
    fi
    if [[ "$NEW_UEFI_VERDICT" == "FAIL" ]]; then
        ok "the UEFI leg is recorded FAIL"
    else
        bad "the UEFI leg is recorded '${NEW_UEFI_VERDICT:-absent}' after it failed to boot"
    fi
    if [[ -z "$NEW_UEFI_KERNEL" ]]; then
        ok "and it records no booted kernel for that leg -- there is nothing for a later run to inherit"
    else
        bad "the FAIL receipt still names kernel $NEW_UEFI_KERNEL as booted: the claim outlived the boot"
    fi
    if [[ "$NEW_UEFI_ISO" == "$MUTANT_SHA" ]]; then
        ok "the leg names the disc it actually measured ($MUTANT_SHA)"
    else
        bad "the UEFI leg records disc ${NEW_UEFI_ISO:-nothing} but the disc was $MUTANT_SHA: the receipt does not describe this run"
    fi
    if [[ "$NEW_BIOS_VERDICT" == "PASS" ]]; then
        ok "and the BIOS leg it booted is still PASS -- per-leg verdicts, not one blanket failure"
    else
        bad "the BIOS leg is recorded '${NEW_BIOS_VERDICT:-absent}' although it booted"
    fi
fi

# ---- restore, and PROVE the restore -------------------------------------
echo
echo "==> restore the real discs"
cp "$TMP/uefi.iso" "$UEFI" || bad "could not put $UEFI back"
cp "$TMP/uefi.iso.sha256" "$UEFI_SHA" || bad "could not put $UEFI_SHA back"
MUTATED=0
if cmp -s "$TMP/uefi.iso" "$UEFI" && cmp -s "$TMP/bios.iso" "$BIOS"; then
    ok "both discs are byte-for-byte what they were before the mutation"
else
    bad "a disc does not match its backup after the restore"
fi
if ( cd dist && sha256sum -c nyrqis-live.iso.sha256 >/dev/null 2>&1 \
        && sha256sum -c nyrqis-live-uefi.iso.sha256 >/dev/null 2>&1 ); then
    ok "and both match their recorded digests again ($GOOD_UEFI_SHA for the UEFI disc)"
else
    bad "a restored disc does not match its recorded digest"
fi

# ---- re-earn the PASS ---------------------------------------------------
echo
echo "==> run 2: the restored discs must earn a fresh PASS"
LOG2="$WORK/check-live-reearned.log"
"$GATE" --boot --uefi-boot > "$LOG2" 2>&1
S2=$?
if [[ "$S2" -eq 0 ]]; then
    ok "the gate passed the restored discs (exit 0)"
else
    bad "the restored discs still fail (exit $S2) -- the tree is left as it was found, but with no PASS"
    tail -12 "$LOG2" | sed 's/^/        /'
fi
RE_VERDICT="$(receipt_field "$RECEIPT" verdict || true)"
RE_BIOS="$(receipt_field "$RECEIPT" legs.bios.booted_kernel_sha256 || true)"
RE_UEFI="$(receipt_field "$RECEIPT" legs.uefi.booted_kernel_sha256 || true)"
if [[ "$RE_VERDICT" == "PASS" && "$RE_BIOS" == "$BUILD_SHA" && "$RE_UEFI" == "$BUILD_SHA" ]]; then
    ok "the minted receipt is a PASS and both legs name kernel $BUILD_SHA"
else
    bad "the re-earned receipt is not a PASS over this kernel (verdict ${RE_VERDICT:-unreadable}, bios ${RE_BIOS:-none}, uefi ${RE_UEFI:-none})"
fi
if grep -qF "the receipt covers this build" "$LOG2"; then
    ok "and the gate says the claim covers this build rather than being stale"
else
    bad "the gate did not confirm the new claim covers this build"
fi

# ---- leave the tree clean ------------------------------------------------
# A minted receipt differs from the recorded one only by its "generated"
# timestamp when it is the same claim. Keeping that would leave dist/ dirty
# for a difference that carries no evidence, so the recorded bytes go back; if
# the claim really changed, the new receipt is kept and this check fails,
# because it must never report PASS over a modified dist/.
echo
echo "==> the tree this run leaves behind"
if same_claim "$TMP/receipt.json" "$RECEIPT"; then
    cp "$TMP/receipt.json" "$RECEIPT"
    ok "the re-earned receipt is the same claim as the recorded one (only 'generated' differed) -- recorded bytes restored"
else
    KEEP_RECEIPT=1
    bad "$RECEIPT is a DIFFERENT claim from the recorded one -- it is kept, but it has to be committed before this check can pass"
fi
DIRTY_AFTER="$(git status --porcelain -- dist)"
if [[ -z "$DIRTY_AFTER" ]]; then
    ok "dist/ is unmodified: no disc, digest or receipt differs from the commit"
else
    bad "dist/ is dirty after the run:
$(printf '%s\n' "$DIRTY_AFTER" | sed 's/^/        /')"
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS live-ISO self-check(s) did not hold -- the gate can pass an unbootable disc or inherit a stale claim"
    exit 1
fi
echo "PASS: a disc whose stub cannot load drove the gate red and minted FAIL with no booted kernel; the restored discs re-earned PASS and dist/ is clean"
