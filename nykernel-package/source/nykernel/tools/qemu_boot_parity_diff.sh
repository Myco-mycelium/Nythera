#!/usr/bin/env bash
#
# qemu_boot_parity_diff.sh -- the transcripts' cross-check: runs BOTH
# boot gates (qemu_boot_test.sh then qemu_uefi_boot_test.sh), extracts
# their guest serial sections, and asserts the KERNEL-side demo chain
# is line-identical between GRUB/Multiboot2-BIOS and native OVMF/UEFI
# after removing ONLY a documented allowlist of lines that are honest
# boot-environment facts, not chain behaviour:
#
#   - the UEFI stub section (everything above the kernel's first banner
#     line: OVMF boot choice, stub banner, memory map, RSDP, MBI
#     synthesis, EBS, CR3 switch -- the 2026-10-22 increment's subject)
#   - `multiboot: memory map parsed` (GRUB's map and the UEFI map
#     differ in usable-frame counts; both are facts about the firmware,
#     not about the kernel)
#   - `acpi:` and `acpi reclaim:` (RSDP v0/RSDT vs v2/XSDT and the
#     presence/absence of REAL AcpiReclaimable regions differ per
#     firmware; the kernel reports honestly either way)
#   - ATA lines (device presence is a machine-configuration fact)
#   - the RTC/timer count lines and the ring-3 preemption counters
#     (nondeterminism by design: wall-clock seconds, tick counts,
#     spin iterations)
#
# A residual diff after allowlist removal is a FAIL with the residual
# printed: the two boot paths ran different KERNEL behaviour, which the
# "same chain on both paths" contract forbids. New demo lines carry -- OK
# facts and are NOT in the allowlist, so an added/changed demo must stay
# identical on both paths or this gate rings.
#
# Lines were catalogued once from the recorded 2026-10-23 back-to-back
# run (IMPLEMENTATION_STATUS.md); the diff there is exactly this set.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> running the BIOS gate (qemu_boot_test.sh)"
./tools/qemu_boot_test.sh > target/parity-bios.txt 2>&1 || {
    echo "FAIL: the BIOS boot gate itself exited nonzero; see target/parity-bios.txt"
    exit 1
}

echo "==> running the UEFI gate (qemu_uefi_boot_test.sh)"
./tools/qemu_uefi_boot_test.sh > target/parity-uefi.txt 2>&1 || {
    echo "FAIL: the UEFI boot gate itself exited nonzero; see target/parity-uefi.txt"
    exit 1
}

# Guest section extraction: between the scripts' serial-output markers;
# the QEMU serial framing adds carriage returns, which must be stripped
# BEFORE the banner-anchored sed, not just cosmetically -- an unstripped
# CRCR makes every line-end regex miss and turns the whole check into a
# silent empty diff (the exact failure the first local run caught).
extract_guest() {
    awk '/guest serial output/{f=1;next} /^-+$/{if(f) exit} f' "$1" | tr -d '\r'
}

# Drop everything above the kernel's first banner (the UEFI stub's
# section; harmless no-op on the BIOS section which starts there), then
# the documented allowlist, then a plain line diff.
normalize() {
    extract_guest "$1" \
    | sed -n '/^nyrqis kernel: phase 1 bring-up$/,$p' \
    | grep -v \
        -e '^  multiboot: memory map parsed' \
        -e '^  acpi: RSDP' \
        -e '^  acpi reclaim:' \
        -e '^  ata' \
        -e '^  rtc self-test:' \
        -e '^  rtc wall-clock self-test:' \
        -e '^  experimental ring3-scheduler preemption self-test: task'
}

normalize target/parity-bios.txt > target/parity-bios.norm.txt
normalize target/parity-uefi.txt > target/parity-uefi.norm.txt

if diff -u target/parity-bios.norm.txt target/parity-uefi.norm.txt > target/parity-residual.diff; then
    echo "PASS: BIOS and UEFI kernels' demo chains are line-identical modulo the documented environment-fact allowlist"
    echo "     (BIOS $(wc -l < target/parity-bios.norm.txt) chain lines == UEFI $(wc -l < target/parity-uefi.norm.txt) chain lines; allowlist deltas only)"
    exit 0
else
    echo "FAIL: residual transcript differences OUTSIDE the documented environment-fact allowlist:"
    cat target/parity-residual.diff
    echo "     These lines differ between the two boot paths and are not catalogued"
    echo "     boot-environment facts -- either a real divergence (fix the kernel/loader)"
    echo "     or a legitimately new environment-fact line (catalogue it in this script"
    echo "     AND in the IMPLEMENTATION_STATUS.md record, with an argument)."
    exit 1
fi
