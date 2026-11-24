#!/usr/bin/env bash
#
# gate_env.sh -- check the environment the boot gates assume, and SAY which
# assumption is broken instead of letting it surface as a 127 or a hang.
#
#   ./tools/gate_env.sh           # human-readable report
#   ./tools/gate_env.sh --quiet   # only failures/warnings (CI step)
#
# Every one of these has already cost a debugging session in this tree:
#
#   * proot resolves a relative socket path against the ABSOLUTE cwd, and a
#     unix socket path is capped at 108 bytes -- so a gate run from the long
#     workspace path fails to create its monitor socket while the same gate
#     passes from a short one. The fix is to run from a short path (the
#     documented `ln -sfn <repo> /tmp/k`), which this script checks.
#   * /usr/bin/time does not exist on every image; a timing wrapper that
#     assumes it turns `build.sh` into exit 127 with no output at all.
#   * clippy is a separate rustup component. When it is missing, the
#     shadowing gate and the abort/truncation ratchet skip cleanly -- which is
#     correct locally and a hole in CI, hence SHADOW_CHECK_REQUIRED.
#   * the QEMU/GRUB pair is not vendored: the gates need both on PATH, at
#     versions that can do multiboot2 + the device modes.
#   * grub-mkrescue is a Unix shell wrapper over grub-mkimage + xorriso with no
#     Windows build, so a Git Bash/MSYS2 host has the module tree but not the
#     wrapper. The disc gates fall back to grub-mkimage (that is how dist/ was
#     built); the gates that assemble their own throwaway ISO cannot, and are
#     named on a "blocked gates" line instead of failing the whole run.
#
# Exit: 0 if the gates can run here, 1 if a hard requirement is missing.
# Warnings never fail the run, and a gate that cannot run is named on a
# "blocked gates" line, so READY is never read as "everything ran".

set -uo pipefail
cd "$(dirname "$0")/.."

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

FAILS=0
WARNS=0
say()  { [[ "$QUIET" == "1" ]] || echo "$*"; }
ok()   { say "  OK: $*"; }
warn() { echo "  WARN: $*"; WARNS=$((WARNS + 1)); }
bad()  { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

say "==> paths"
PWD_ABS="$(pwd -P)"
say "  cwd: $PWD_ABS ($(printf '%s' "$PWD_ABS" | wc -c | tr -d ' ') bytes)"
if [[ "$(printf '%s' "$PWD_ABS" | wc -c | tr -d ' ')" -gt 90 ]]; then
    warn "long path: proot resolves relative socket paths against it and unix sockets cap at 108 bytes; run the gates from a short symlink (e.g. /tmp/k -> this tree)"
else
    ok "the path leaves room for a 108-byte unix socket name"
fi

say "==> tools"
need() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $("$1" --version 2>&1 | head -1)"
    else
        bad "$1 is not on PATH${2:+ ($2)}"
    fi
}
opt() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $("$1" --version 2>&1 | head -1)"
    else
        warn "$1 is not on PATH${2:+ ($2)}"
    fi
}
need cargo "the kernel cannot build without it"
need rustc
need python3 "tools/check_svc84_ledger.py and tools/lint_ratchet.py need it"
need qemu-system-x86_64 "all three boot gates need it"
# grub-mkrescue assembles the throwaway test ISOs four gates boot. It is a
# separate Linux package (grub-pc-bin) and has no Windows build, so its absence
# is a normal host shape, not a broken environment: the disc gates do not use
# it -- make_live_iso.sh falls back to grub-mkimage, which is how dist/ was
# produced here. What it costs is the four gates that build their own ISO, and
# they are named once, on the blocked-gates line below.
BLOCKED_GATES=""
if command -v grub-mkrescue >/dev/null 2>&1; then
    ok "grub-mkrescue: $(grub-mkrescue --version 2>&1 | head -1)"
elif command -v grub-mkimage >/dev/null 2>&1; then
    BLOCKED_GATES="cursor_test.sh device_mode_test.sh svc84_proof_test.sh qemu_boot_test.sh"
    warn "grub-mkrescue is not on PATH (grub-mkimage is: make_live_iso.sh still writes both discs); the gates that assemble their own test ISO are listed as blocked below"
else
    bad "neither grub-mkrescue nor grub-mkimage is on PATH: no ISO can be assembled here, neither the discs nor a test image"
fi
# proot is not on PATH by design: tools/install_boot_tools.sh puts it in its
# own prefix and the /tmp/nybin shims exec it from there. Look in that prefix
# before warning, so "proot missing" means missing rather than "not on PATH".
TOOLS_PREFIX="${NYRQIS_BOOT_TOOLS:-$HOME/.cache/nyrqis-boot-tools}"
if command -v proot >/dev/null 2>&1; then
    ok "proot: $(command -v proot)"
elif [[ -x "$TOOLS_PREFIX/proot" ]]; then
    ok "proot: $TOOLS_PREFIX/proot (rootless boot tools prefix)"
else
    warn "proot is not on PATH and $TOOLS_PREFIX/proot is missing: rerun tools/install_boot_tools.sh for the rootless path"
fi
opt timeout "gates bound their QEMU wait with it"

if cargo clippy --version >/dev/null 2>&1; then
    ok "clippy: $(cargo clippy --version 2>&1 | head -1) (shadowing gate + abort ratchet run)"
else
    warn "clippy is not installed: the shadowing gate and the abort/truncation ratchet will SKIP (set SHADOW_CHECK_REQUIRED=1 to make that a failure)"
fi

if [[ -x /usr/bin/time ]]; then
    ok "/usr/bin/time exists"
else
    warn "/usr/bin/time does not exist here: time commands with the shell's own 'time' keyword or date; a wrapper that runs it exits 127 before doing anything"
fi

say "==> writable state"
for d in target /tmp; do
    if [[ -w "$d" ]]; then ok "$d is writable"; else bad "$d is not writable"; fi
done
if [[ -d /tmp/nybin ]]; then
    ok "/tmp/nybin shims present (rootless qemu/grub wrappers)"
elif command -v qemu-system-x86_64 >/dev/null 2>&1; then
    say "  (no /tmp/nybin: using whatever qemu/grub are on PATH)"
else
    warn "/tmp/nybin is missing and no qemu resolves: rerun tools/install_boot_tools.sh"
fi

say "==> opt-outs"
say "  SKIP_SHADOW_CHECK=${SKIP_SHADOW_CHECK:-0} SKIP_LINT_RATCHET=${SKIP_LINT_RATCHET:-0} SHADOW_CHECK_REQUIRED=${SHADOW_CHECK_REQUIRED:-0}"

say ""
# echo, not say: which gates did not run must survive --quiet.
if [[ -n "$BLOCKED_GATES" ]]; then
    echo "gate_env: blocked gates (grub-mkrescue missing): $BLOCKED_GATES"
fi
if [[ "$FAILS" -ne 0 ]]; then
    echo "gate env: NOT READY -- $FAILS hard requirement(s) missing, $WARNS warning(s)"
    exit 1
fi
echo "gate env: READY ($WARNS warning(s))"
