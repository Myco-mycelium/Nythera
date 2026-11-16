#!/usr/bin/env bash
#
# install_boot_tools.sh -- install qemu-system-x86_64, grub-mkrescue, xorriso
# and mtools WITHOUT root, for the host gates (qemu_boot_test.sh,
# device_mode_test.sh, svc84_proof_test.sh).
#
#   ./tools/install_boot_tools.sh              # install into $PREFIX
#   ./tools/install_boot_tools.sh --check      # report whether it is usable
#   ./tools/install_boot_tools.sh --prefix DIR # elsewhere (default below)
#
# Why this exists: the gates need a real emulated machine and a real GRUB
# El Torito image. On a normal desktop or CI image that is `apt-get install
# qemu-system-x86 grub-pc-bin xorriso mtools`, but the Freebuff cloud VM runs
# this checkout as an unprivileged user with the "no new privileges" flag
# set, so sudo cannot work at all. The unprivileged equivalent is a user-space
# root: proot (a static ptrace-based chroot, no root required) plus an Ubuntu
# base rootfs, with the packages installed INTO that rootfs by its own apt.
#
# The resulting shims in $PREFIX/bin wrap each tool in
#   proot -0 -R $PREFIX/rootfs -b /proc -b /dev -b /sys -b /tmp -b /run \
#         -b /workspace -w "$PWD" /usr/bin/<tool> "$@"
# so the tools see the checkout at its real path (the ISO, the serial log and
# the screenshot all land where the gate scripts expect them) while running
# against the rootfs's own glibc and libraries. `-b /tmp` matters: the device
# gate talks to QEMU over a monitor socket, and a unix socket path is capped
# at 108 bytes.
#
# AFTER INSTALLING, the gates must be run with the shims FIRST on PATH, from a
# SHORT path. Both are the same limit: proot resolves a relative path to its
# absolute form before bind(), so a socket under
# /workspace/chats/<id>/<repo>/target/... (120 bytes) fails to create. Use a
# symlink with a short name:
#
#   export PATH="$PREFIX/bin:$PATH"
#   ln -sfn "$PWD" /tmp/k && cd /tmp/k
#   ./tools/qemu_boot_test.sh && ./tools/device_mode_test.sh \
#       && ./tools/svc84_proof_test.sh
#
# Nothing here touches the host: no root, no system packages, no /etc edits.
# The rootfs is a directory, the shims are files in it, and deleting $PREFIX
# undoes the whole install.

set -uo pipefail

PREFIX="${BOOT_TOOLS_PREFIX:-$HOME/.cache/nyrqis-boot-tools}"
PROOT_URL="https://proot.gitlab.io/proot/bin/proot"
ROOTFS_URL="https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.3-base-amd64.tar.gz"
PACKAGES="qemu-system-x86 grub-pc-bin grub-common xorriso mtools"
TOOLS="qemu-system-x86_64 grub-mkrescue grub-mkimage xorriso mformat mcopy"

ok()   { echo "  OK: $*"; }
bad()  { echo "  FAIL: $*" >&2; }
step() { echo "==> $*"; }

case "${1:-}" in
    --prefix) PREFIX="${2:?--prefix needs a directory}";;
    --check)
        missing=0
        for t in $TOOLS; do
            command -v "$t" >/dev/null 2>&1 || { bad "$t not on PATH"; missing=1; }
        done
        qemu-system-x86_64 --version 2>/dev/null | head -1 | sed 's/^/  /'
        grub-mkrescue --version 2>/dev/null | head -1 | sed 's/^/  /'
        [[ $missing -eq 0 ]] && ok "boot tooling is usable from PATH=$PATH"
        exit "$missing"
        ;;
    -h|--help) sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    "") ;;
    *) bad "unknown argument $1"; exit 2;;
esac

step "prefix: $PREFIX"
mkdir -p "$PREFIX/bin" "$PREFIX/rootfs" || { bad "cannot create $PREFIX"; exit 1; }

# ---- 1. proot (static, no root) ----------------------------------------
if [[ ! -x "$PREFIX/proot" ]]; then
    step "fetching proot"
    curl -fsSL -o "$PREFIX/proot" "$PROOT_URL" || { bad "proot download failed"; exit 1; }
    chmod +x "$PREFIX/proot"
fi
"$PREFIX/proot" --version >/dev/null 2>&1 || { bad "$PREFIX/proot does not run"; exit 1; }
ok "proot present"

# ---- 2. a user-space Ubuntu rootfs -------------------------------------
if [[ ! -x "$PREFIX/rootfs/usr/bin/apt-get" ]]; then
    step "fetching the Ubuntu base rootfs (this is the only big download)"
    curl -fsSL -o "$PREFIX/ubuntu-base.tar.gz" "$ROOTFS_URL" \
        || { bad "rootfs download failed"; exit 1; }
    tar -xzf "$PREFIX/ubuntu-base.tar.gz" -C "$PREFIX/rootfs" \
        || { bad "rootfs extraction failed"; exit 1; }
fi
# The rootfs has no resolver of its own until one is copied in.
if [[ ! -s "$PREFIX/rootfs/etc/resolv.conf" ]]; then
    cp /etc/resolv.conf "$PREFIX/rootfs/etc/resolv.conf" 2>/dev/null \
        || echo "nameserver 1.1.1.1" >"$PREFIX/rootfs/etc/resolv.conf"
fi
ok "rootfs unpacked"

run_in_rootfs() {
    "$PREFIX/proot" -0 -R "$PREFIX/rootfs" -b /proc -b /dev -b /sys \
        -w /root /usr/bin/env DEBIAN_FRONTEND=noninteractive "$@"
}

# ---- 3. the packages ---------------------------------------------------
if [[ ! -x "$PREFIX/rootfs/usr/bin/qemu-system-x86_64" ]]; then
    step "apt-get update (inside the rootfs)"
    run_in_rootfs apt-get update || { bad "apt-get update failed"; exit 1; }
    step "installing $PACKAGES"
    # Some postinst scripts (tzdata, python3) cannot finish without a real
    # init and report failures; the BINARIES and GRUB's module tree land
    # regardless, which is all these gates need. If a tool is still missing
    # after this, the install is genuinely broken and --check will say so.
    run_in_rootfs apt-get install -y --no-install-recommends $PACKAGES || true
fi

# ---- 4. shims on PATH --------------------------------------------------
step "writing shims into $PREFIX/bin"
for t in $TOOLS; do
    [[ -x "$PREFIX/rootfs/usr/bin/$t" ]] || { bad "rootfs is missing /usr/bin/$t"; exit 1; }
    cat >"$PREFIX/bin/$t" <<EOF
#!/bin/sh
# Generated by nyrqis tools/install_boot_tools.sh -- runs the rootfs's $t
# against this checkout, with no root and no host packages.
exec $PREFIX/proot -0 -R $PREFIX/rootfs \\
    -b /proc -b /dev -b /sys -b /tmp -b /run -b /workspace \\
    -w "\$PWD" /usr/bin/$t "\$@"
EOF
    chmod +x "$PREFIX/bin/$t"
done

step "verifying"
PATH="$PREFIX/bin:$PATH" "$0" --check || exit 1

cat <<EOF

==> installed. Run the boot gates with:

    export PATH="$PREFIX/bin:\$PATH"
    ln -sfn "\$PWD" /tmp/k && cd /tmp/k
    ./tools/qemu_boot_test.sh
    ./tools/device_mode_test.sh
    ./tools/svc84_proof_test.sh

(The short /tmp/k path is required: a unix socket's path is capped at 108
bytes and proot resolves relative paths to their absolute form before bind.)
EOF
