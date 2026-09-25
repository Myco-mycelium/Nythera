#!/usr/bin/env bash
# build-live-iso-rootless.sh — build the Nyrqis live ISO WITHOUT root.
#
# Runs the unmodified packaging/live/build-live-iso.sh inside a user
# namespace (unshare -Urmpf --mount-proc) with a tiny LD_PRELOAD shim
# (rootless-syscall-shim.c) that makes the two operations a user
# namespace genuinely cannot do behave correctly for this build:
#
#   chown  -> no-op success (inside the self-map every file the build
#             creates is already owned by the one uid that maps to root;
#             image ownership is correct by construction),
#   mknod  -> char/block devices become placeholder regular files (the
#             booted system mounts devtmpfs; nothing in the image
#             depends on build-time device nodes).
#
# WHY NOT the alternatives (all probed on the reference machine):
#   --map-auto / newuidmap  util-linux shells out to newuidmap, which is
#                           absent (setuid helper) and needs /etc/subuid
#                           delegation. Self-map (-U/-Ur) needs neither.
#   fakeroot + daemon       the faked daemon performs REAL chowns and
#                           propagates EINVAL from unmapped gids inside
#                           the self-map (only EPERM is swallowed) —
#                           tar -xf of a root:staff file still fails.
#   proot / mmdebstrap      not installed on the reference machine.
#
# The result is a byte-for-byte ordinary ISO: same builder, same gates
# (dpkg audit, probe parity, byte-compile, initrd verification, size
# gate), same grub-mkrescue assembly — no root anywhere.
#
# Usage:
#   packaging/live/build-live-iso-rootless.sh \
#       --output dist/nyrqis-live-rootless.iso [--keep-workdir] [extra args]
#
# Two-phase use (resumable acquisition; useful when the runtime bounds
# foreground command time or the network is flaky):
#   packaging/live/build-live-iso-rootless.sh --acquire-rootfs /srv/lr
#   ... rerun the same command after interruptions: the .deb cache
#   (DIR.cache, beside the target) survives, a partial TARGET is wiped,
#   and a rerun costs extraction only — no redownload. A completion
#   stamp (DIR.complete) makes reruns no-ops.
#   packaging/live/build-live-iso-rootless.sh \
#       --rootfs /srv/lr --output dist/nyrqis-live-rootless.iso
#
# Extra args (e.g. --arch arm64, --volume-id, --rootfs) are passed
# through to build-live-iso.sh. The driver never modifies the builder.
#
# Requirements (all checked): unshare with user namespaces enabled,
# gcc/cc (shim compile), and the builder's own toolset (checked by the
# builder itself).
#
# Chrooted steps (debootstrap stage 2, apt, useradd, mkinitramfs) get
# the shim via a pre-staged copy in the target's /tmp plus a generated
# `chroot` wrapper — see the handoff section.
#
# Exit codes: 0 = ISO built; 1 = preconditions or build failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$SCRIPT_DIR/build-live-iso.sh"
SHIM_SRC="$SCRIPT_DIR/rootless-syscall-shim.c"
BUILDER_NAME="$(basename "$BUILDER")"
SHIM_SO_NAME="$(basename "$SHIM_SRC" .c).so"

log()   { printf '\e[1;35m[rootless]\e[0m %s\n' "$*"; }
die()   { printf '\e[1;31m[rootless] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- args
OUTPUT="" ; POSITIONAL=() ; WORKDIR="" ; ACQUIRE_ROOTFS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output|-o)    OUTPUT="$2"; shift 2 ;;
        --keep-workdir) POSITIONAL+=("$1"); shift ;;
        --workdir)      WORKDIR="$2"; POSITIONAL+=("$1" "$2"); shift 2 ;;
        --acquire-rootfs) ACQUIRE_ROOTFS="$2"; shift 2 ;;
        --help|-h)      sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              POSITIONAL+=("$1"); shift ;;
    esac
done
[[ -x "$BUILDER" || -f "$BUILDER" ]] || die "builder not found: $BUILDER"
[[ -f "$SHIM_SRC" ]] || die "shim source not found: $SHIM_SRC"

# ---------------------------------------------------------------- preconditions
command -v unshare >/dev/null 2>&1 || die "unshare not on PATH"
command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 \
    || die "no C compiler (cc/gcc) — the shim must be compiled once"

# Kernel/userns probes — with the exact reason each failure means.
unshare -Ur true 2>/dev/null \
    || die "user namespaces unavailable (unshare -Ur failed).
  Fixes: sysctl kernel.unprivileged_userns_clone=1 (Debian),
  or apparmor may block unprivileged userns (Ubuntu 24.04+:
  sysctl apparmor_restrict_unprivileged_userns=0)."

unshare -Urmpf --mount-proc true 2>/dev/null \
    || die "unshare -Urmpf --mount-proc failed — mount/pid namespaces restricted (this driver needs both)"

# ---------------------------------------------------------------- compile shim
SHIM_SO="$(mktemp /tmp/nyrqis-shim.XXXXXX.so)"
# Ownership overrides for the forced-0:0 squashfs: the demo user's HOME
# must be 1000:1000 in the image (sudo hard-refuses a /etc/sudoers not
# owned by 0 — and a root-owned HOME would trip exactly that check at
# boot; useradd-style 0755 is what a normal build produces).
PSEUDO_DEFS="$(mktemp /tmp/nyrqis-pseudo.XXXXXX)"
printf '/home/demo m 0755 1000 1000\n/home/demo/.bash_profile m 0644 1000 1000\n' \
    > "$PSEUDO_DEFS"
trap 'rm -f "$SHIM_SO" "$PSEUDO_DEFS"' EXIT
CC_BIN=cc; command -v cc >/dev/null 2>&1 || CC_BIN=gcc
"$CC_BIN" -O2 -fPIC -shared -o "$SHIM_SO" "$SHIM_SRC" -ldl \
    || die "shim compile failed"

# ---------------------------------------------------------------- handoff
# The shim must be visible at the SAME absolute path inside every chroot
# the builder performs. Bind-mounting host paths into the namespace is
# EPERM (mounts owned by the initial user namespace cannot be rebound
# from a child userns), so two mechanisms cooperate:
#   1. a pre-staged copy of the .so in the target's /tmp (debootstrap's
#      internal chroot calls do not inherit this shell's PATH);
#   2. a generated `chroot` wrapper earlier in PATH that copies the .so
#      into the target before every real chroot (builder steps like the
#      apt top-up, useradd, mkinitramfs).
# A load failure inside a chroot is a loader WARNING, not fatal — and
# would surface as chown errors in the builder's own gates anyway.
SHIMBIN="$(mktemp -d /tmp/nyrqis-shimbin.XXXXXX)"
cat > "$SHIMBIN/chroot" <<WRAPPER
#!/bin/sh
# Rootless-build chroot wrapper (generated by build-live-iso-rootless.sh):
# stage the preload shim into the target tree, then exec the real chroot.
SHIM_SO="$SHIM_SO"
tgt=""
for a in "\$@"; do
    case "\$a" in
        -*) continue ;;
        *)  if [ -d "\$a" ] && [ "\$a" != "/" ]; then tgt="\$a"; break; fi ;;
    esac
done
if [ -n "\$tgt" ] && [ -f "\$SHIM_SO" ]; then
    mkdir -p "\$tgt/tmp" 2>/dev/null
    cp -f "\$SHIM_SO" "\$tgt/tmp/" 2>/dev/null || :
fi
exec /usr/sbin/chroot "\$@"
WRAPPER
chmod 0755 "$SHIMBIN/chroot"
trap 'rm -rf "$SHIMBIN"; rm -f "$SHIM_SO" "$PSEUDO_DEFS"' EXIT

log "compiling shim: $CC_BIN → $SHIM_SO"

# ------------------------------------------------------------ acquisition-only
# --acquire-rootfs DIR: run ONLY the debootstrap step into DIR and exit.
# The builder's --rootfs path then consumes it. Splits the long
# download+install phase from the (fast) ISO assembly so each fits a
# bounded foreground runtime window; resumable via the cache dir.
if [[ -n "$ACQUIRE_ROOTFS" ]]; then
    SUITE="${NYRQIS_LIVE_SUITE:-bookworm}"
    MIRROR="${NYRQIS_LIVE_MIRROR:-http://deb.debian.org/debian}"
    DEB_ARCH=amd64; KERNEL_PKG=linux-image-amd64
    STAMP="$ACQUIRE_ROOTFS.complete"
    CACHE="${NYRQIS_ROOTLESS_CACHE_DIR:-$ACQUIRE_ROOTFS.cache}"
    if [[ -f "$STAMP" ]]; then
        log "rootfs already complete: $ACQUIRE_ROOTFS (stamp present)"
        exit 0
    fi
    # Partial tree from an interrupted run: wipe the TARGET, keep the
    # CACHE. Re-extracting into a populated tree collides (tar "File
    # exists"); from a warm cache a redo costs extraction only — no
    # redownload. The stamp is a SIBLING (the directory is consumed
    # verbatim by the builder's --rootfs path).
    if [[ -d "$ACQUIRE_ROOTFS" && -n "$(ls -A "$ACQUIRE_ROOTFS" 2>/dev/null)" ]]; then
        log "removing partial rootfs at $ACQUIRE_ROOTFS (cache at $CACHE is preserved)"
        rm -rf "$ACQUIRE_ROOTFS"
    fi
    log "acquiring rootfs (debootstrap $SUITE/$DEB_ARCH) into $ACQUIRE_ROOTFS"
    log "  mirror=$MIRROR cache=$CACHE"
    mkdir -p "$ACQUIRE_ROOTFS" "$CACHE"
    # Stage the shim into the target BEFORE debootstrap starts: the
    # inherited LD_PRELOAD path is absolute and the target's /tmp exists
    # from the first chroot on, so the preload resolves inside every
    # chrooted step (dpkg postinsts chown /var/mail etc. — the exact
    # failure without it).
    mkdir -p "$ACQUIRE_ROOTFS/tmp"
    cp -f "$SHIM_SO" "$ACQUIRE_ROOTFS/tmp/"
    # Direct debootstrap call — deliberately NOT the builder: acquisition
    # alone cannot pass the builder's chroot gates, and this phase needs
    # the cache-dir resume story (a --cache-dir OUTSIDE the target
    # survives the wipe-and-retry above). Drift between this include
    # list and the builder's is harmless BY CONSTRUCTION: the builder's
    # ensure-packages step installs missing probe packages on the
    # --rootfs path, and its dpkg-audit + probe-parity gates fail the
    # build if anything is still absent.
    set +e
    unshare -Urmpf --mount-proc env \
        LD_PRELOAD="$SHIM_SO" \
        PATH="$SHIMBIN:$PATH" \
        debootstrap --variant=minbase --arch="$DEB_ARCH" \
            --cache-dir="$CACHE" \
            --include=systemd,systemd-sysv,sudo,$KERNEL_PKG,live-boot,live-boot-initramfs-tools,python3,python3-zstandard,python3-cffi,python3-ply,python3-nacl,python3-lz4,fuse3 \
            "$SUITE" "$ACQUIRE_ROOTFS" "$MIRROR"
    RC=$?
    set -e
    if [ "$RC" -eq 0 ]; then
        touch "$STAMP"
        log "acquisition complete (stamp written)"
    fi
    exit "$RC"
fi

# ---------------------------------------------------------------- build
log "launching $BUILDER_NAME in a user namespace (no root involved)"
log "extra builder args: ${POSITIONAL[*]:-(none)}"

set +e
BUILDER_ARGS=()
[[ -n "$OUTPUT" ]] && BUILDER_ARGS+=(--output "$OUTPUT")
unshare -Urmpf --mount-proc env \
    LD_PRELOAD="$SHIM_SO" \
    PATH="$SHIMBIN:$PATH" \
    NYRQIS_ROOTLESS_BUILD=1 \
    NYRQIS_SQUASHFS_FORCE_ROOT=1 \
    NYRQIS_SQUASHFS_PSEUDO="$PSEUDO_DEFS" \
    bash "$BUILDER" \
    "${BUILDER_ARGS[@]}" \
    "${POSITIONAL[@]}"
RC=$?
set -e

# The builder's die() messages already carry ::error:: annotations; keep
# the rootless layer's verdict distinct so a failure is attributable.
if [ "$RC" -eq 0 ]; then
    log "rootless build OK — ISO produced by the unmodified builder, no root used"
else
    printf '\e[1;31m[rootless] ERROR:\e[0m builder exited rc=%s (see its output above)\n' "$RC" >&2
    exit "$RC"
fi
