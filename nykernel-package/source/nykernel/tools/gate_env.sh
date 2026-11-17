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
#
# Exit: 0 if the gates can run here, 1 if a hard requirement is missing.
# Warnings never fail the run; they name a trap that is armed.

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
need grub-mkrescue "the gates build a bootable ISO with it"
opt proot "rootless boot path only (tools/install_boot_tools.sh)"
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
if [[ "$FAILS" -ne 0 ]]; then
    echo "gate env: NOT READY -- $FAILS hard requirement(s) missing, $WARNS warning(s)"
    exit 1
fi
echo "gate env: READY ($WARNS warning(s))"
