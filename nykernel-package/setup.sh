#!/usr/bin/env bash
#
# setup.sh -- take a fresh Debian/Ubuntu host to a complete, boot-verified
# nykernel build with no manual steps.
#
# It installs every host dependency the kernel needs, ensures a Rust
# toolchain with the `rust-src` component (required by `-Z build-std` for
# the freestanding target), fixes the one packaging quirk this repo has hit
# (a dangling `/usr/bin/rust-lld` symlink on Ubuntu), then builds the kernel
# and runs the automated QEMU boot test.
#
# Idempotent: safe to re-run; already-installed pieces are skipped.
#
# Non-interactive sudo: set SUDO_PASSWORD to avoid a password prompt, e.g.
#
#     SUDO_PASSWORD=... ./setup.sh
#
# (If SUDO_PASSWORD is unset, `sudo` prompts normally. Running as root needs
# no sudo at all.)
#
# Usage: ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

log() { printf '\n==> %s\n' "$*"; }

# --- privilege handling ----------------------------------------------------
run_sudo() {
    if [[ "$(id -u)" -eq 0 ]]; then
        "$@"
    elif [[ -n "${SUDO_PASSWORD:-}" ]]; then
        echo "$SUDO_PASSWORD" | sudo -S -p '' "$@"
    else
        sudo "$@"
    fi
}

# --- 1. host packages ------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
    log "installing host packages (apt)"
    run_sudo env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    run_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        build-essential binutils lld \
        grub-pc-bin grub-common xorriso mtools \
        qemu-system-x86 curl ca-certificates
else
    log "apt-get not found; skipping package install (ensure: binutils, lld, grub-mkrescue, xorriso, mtools, qemu-system-x86_64)"
fi

# Ubuntu ships /usr/bin/rust-lld as a symlink to a versioned lld (e.g.
# rust-lld -> lld-17). If that target package isn't installed the symlink
# dangles and `rust-lld` is unresolvable even after installing `lld` (which
# provides lld-18, not lld-17). Repoint it at whatever lld-* exists.
if [[ -L /usr/bin/rust-lld && ! -e /usr/bin/rust-lld ]]; then
    for cand in /usr/bin/lld-[0-9]*; do
        if [[ -x "$cand" ]]; then
            log "repointing dangling /usr/bin/rust-lld -> $cand"
            run_sudo ln -sf "$cand" /usr/bin/rust-lld
            break
        fi
    done
fi

# --- 2. Rust toolchain + rust-src -----------------------------------------
if ! command -v rustup >/dev/null 2>&1; then
    log "installing rustup (stable)"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain stable
    # shellcheck disable=SC1091
    . "$HOME/.cargo/env"
fi

log "ensuring the rust-src component is installed"
rustup component add rust-src

# --- 3. build + boot-verify ------------------------------------------------
log "building the kernel and running the automated QEMU boot test"
cd source/nykernel
./build.sh
./tools/qemu_boot_test.sh

log "setup complete: kernel builds and passes the full boot test"
