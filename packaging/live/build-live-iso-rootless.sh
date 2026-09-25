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
# TIP: pass --workdir DIR (under $HOME) on the build phase — the ISO
# assembly (squashfs + grub-mkrescue) runs for many minutes and hosts
# running systemd-tmpfiles may EMPTY /tmp underneath it (see WORKROOT
# below). The driver's own temp artifacts already live in $HOME/.cache.
# arm64 (cross, still rootless):
#   packaging/live/build-live-iso-rootless.sh --arch arm64 \
#       --acquire-rootfs /srv/lr-arm64
#   packaging/live/build-live-iso-rootless.sh --arch arm64 \
#       --rootfs /srv/lr-arm64 --output dist/nyrqis-live-arm64.iso
#   Acquisition runs debootstrap --foreign (stage 1 extracts with host
#   binaries); the SECOND stage is chrooted and therefore emulated
#   (qemu-aarch64-static + binfmt_misc). Emulated processes cannot
#   preload an amd64 .so — glibc rejects the ELF class — so the driver
#   ALSO compiles the shim as an AARCH64 shared object. The cross
#   compiler is user-space, no root needed: zig
#   (https://ziglang.org/download — a static tarball suffices), or any
#   aarch64-capable compiler via NYRQIS_AARCH64_CC, e.g.
#   NYRQIS_AARCH64_CC='aarch64-linux-gnu-gcc'. The preload path is
#   CANONICAL (/tmp/rootless-syscall-shim.so): it resolves on BOTH
#   sides of every chroot — host-side the host-class copy, in-target
#   the guest-class copy — so chrooted steps are shimmed even when the
#   chroot wrapper never engages (a PATH-sanitizing debootstrap can
#   skip it; observed on GitHub's 24.04 runner).
#
# Extra args (--volume-id, --rootfs, ...) are passed through to
# build-live-iso.sh (--arch is honored by BOTH phases: acquire uses it
# for the debootstrap arch, build forwards it to the builder). The
# driver never modifies the builder.
#
# Requirements (all checked): unshare with user namespaces enabled,
# gcc/cc (host shim compile), and the builder's own toolset (checked by
# the builder itself). arm64 additionally needs: zig (or
# NYRQIS_AARCH64_CC), qemu-user-static with binfmt_misc registered, and
# grub-efi-arm64's module tree for grub-mkrescue.
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

# Durable temp root. /tmp is NOT safe for long builds on hosts running
# systemd-tmpfiles (tmpfiles.d: 'D /tmp' EMPTIES the directory whenever
# systemd-tmpfiles-setup runs — observed twice on the reference machine,
# the second time MID-BUILD: the shim .so vanished and debootstrap's
# tar started failing on unmapped chowns again). Everything the driver
# produces for its own use lives under $HOME/.cache, which survives.
WORKROOT="${NYRQIS_ROOTLESS_WORKROOT:-$HOME/.cache/nyrqis-rootless}"
mkdir -p "$WORKROOT"

# Arch-wide facts, mirrored from the builder (kept in sync BY TEST: the
# builder computes the same DEB_ARCH/KERNEL_PKG). QEMU_STATIC is the
# qemu-user-static BINARY name — binfmt registers the qemu arch
# (aarch64), which differs from the deb arch (arm64); "qemu-arm64-static"
# does not exist.
arch_facts() {
    case "$1" in
        amd64) DEB_ARCH=amd64; KERNEL_PKG=linux-image-amd64
               FOREIGN=(); QEMU_STATIC="qemu-amd64-static" ;;
        arm64) DEB_ARCH=arm64; KERNEL_PKG=linux-image-arm64
               FOREIGN=(--foreign); QEMU_STATIC="qemu-aarch64-static" ;;
        *)     return 1 ;;
    esac
}

# ---------------------------------------------------------------- args
OUTPUT="" ; POSITIONAL=() ; WORKDIR="" ; ACQUIRE_ROOTFS="" ; ARCH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output|-o)    OUTPUT="$2"; shift 2 ;;
        --arch)         ARCH="$2"; shift 2 ;;
        --keep-workdir) POSITIONAL+=("$1"); shift ;;
        --workdir)      WORKDIR="$2"; POSITIONAL+=("$1" "$2"); shift 2 ;;
        --acquire-rootfs) ACQUIRE_ROOTFS="$2"; shift 2 ;;
        --help|-h)      sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              POSITIONAL+=("$1"); shift ;;
    esac
done
[[ -x "$BUILDER" || -f "$BUILDER" ]] || die "builder not found: $BUILDER"
[[ -f "$SHIM_SRC" ]] || die "shim source not found: $SHIM_SRC"
arch_facts "${ARCH:-amd64}" || die "unsupported --arch '${ARCH:-amd64}' (amd64 | arm64)"
if [[ "$ARCH" == arm64 ]]; then
    [[ -x "/usr/bin/$QEMU_STATIC" ]] || \
        die "$QEMU_STATIC not present — install qemu-user-static"
    [[ -d /proc/sys/fs/binfmt_misc && -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]] \
        || die "binfmt_misc has no qemu-aarch64 registration — the emulated second stage cannot exec arm64 ELF
  (install qemu-user-static and confirm: ls /proc/sys/fs/binfmt_misc | grep aarch64)"
fi

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
SHIM_SO="$(mktemp "$WORKROOT/nyrqis-shim.XXXXXX.so")"
# Ownership overrides for the forced-0:0 squashfs: the demo user's HOME
# must be 1000:1000 in the image (sudo hard-refuses a /etc/sudoers not
# owned by 0 — and a root-owned HOME would trip exactly that check at
# boot; useradd-style 0755 is what a normal build produces).
PSEUDO_DEFS="$(mktemp "$WORKROOT/nyrqis-pseudo.XXXXXX")"
printf '/home/demo m 0755 1000 1000\n/home/demo/.bash_profile m 0644 1000 1000\n' \
    > "$PSEUDO_DEFS"
CC_BIN=cc; command -v cc >/dev/null 2>&1 || CC_BIN=gcc
log "compiling shim: $CC_BIN → $SHIM_SO"
"$CC_BIN" -O2 -fPIC -shared -o "$SHIM_SO" "$SHIM_SRC" -ldl \
    || die "shim compile failed"
# Stage the HOST-class copy at /tmp/$SHIM_SO_NAME (host side). /tmp/$SHIM_SO_NAME
# is the ONE preload path for the whole build: it resolves on both sides of
# every chroot boundary — host-side this copy, in-target the copy acquire
# pre-stages (same name, correct class per side). The outer LD_PRELOAD below
# references it so chrooted steps are shimmed EVEN WHEN the chroot wrapper
# never engages: newer Ubuntu debootstrap sanitizes PATH, so "chroot" may
# resolve to the real /usr/sbin/chroot (observed on GitHub's 24.04 runner —
# the acquire failed unshimmed while the identical flow worked locally).
# Atomic staging helper. ONE implementation for every site that lands a
# shim copy on a preload path (driver init, chroot wrapper host-side +
# in-target, phase re-staging): write to a temp name in the DESTINATION
# directory, then RENAME. Never cp -f straight onto the destination —
# truncating a mapped .so makes running processes execute zeros (SIGSEGV,
# observed as cp+bash cores); rename(2) swaps the entry atomically and
# the old inode stays alive for existing mappers.
STAGE_SHIM="$WORKROOT/nyrqis-stage-shim.$$"
cat > "$STAGE_SHIM" <<'STAGESHIM'
#!/bin/sh
# stage-shim SRC DEST — atomic install of SRC at DEST (preload-safe).
src="$1"; dest="$2"
[ -f "$src" ] && [ -n "$dest" ] || exit 1
d="${dest%/*}"
mkdir -p "$d" 2>/dev/null || exit 1
tmp="$d/.shim.tmp.$$"
cp -f "$src" "$tmp" 2>/dev/null || { rm -f "$tmp"; exit 1; }
mv -f "$tmp" "$dest" || { rm -f "$tmp"; exit 1; }
exit 0
STAGESHIM
chmod 0755 "$STAGE_SHIM"
"$STAGE_SHIM" "$SHIM_SO" "/tmp/$SHIM_SO_NAME" \
    || die "staging the host shim at /tmp/$SHIM_SO_NAME failed"
# Watchdog: re-stage the canonical host-side copy every minute for the
# WHOLE run (acquire AND build). Hosts running systemd-tmpfiles can
# empty /tmp at any moment (observed twice locally): already-running
# processes keep their inode mappings, but every NEW exec — debootstrap's
# constant tar|dpkg pipelines included — would silently lose the preload.
stage_watchdog() {
    while :; do
        sleep 60
        "$STAGE_SHIM" "$SHIM_SO" "/tmp/$SHIM_SO_NAME" 2>/dev/null || :
    done
}
cleanup() {
    [[ -n "${WATCHDOG_PID:-}" ]] && kill "$WATCHDOG_PID" 2>/dev/null || :
    rm -rf "${SHIMBIN:-}" "${GUEST_TMP:-}" "${STAGE_SHIM:-}"
    rm -f "$SHIM_SO" "$PSEUDO_DEFS" "/tmp/$SHIM_SO_NAME"
}
trap cleanup EXIT

# ---------------------------------------------------------------- cross shim (arm64)
# The emulated (second-stage) chroot runs AARCH64 binaries: glibc refuses
# to load an amd64 preload into them ("ELF file ... is for a different
# host architecture"), so the chrooted steps would run UN-shimmed and
# their chowns would hard-fail. The guest-class shim is cross-compiled
# per build into a temp dir (never committed) — zig needs no root and no
# system packages; an installed aarch64 cross-gcc wins via env override.
GUEST_SHIM=""
if [[ "$ARCH" == arm64 ]]; then
    GUEST_TMP="$(mktemp -d "$WORKROOT/nyrqis-guestshim.XXXXXX")"
    if [[ -n "${NYRQIS_AARCH64_CC:-}" ]]; then
        log "cross-compiling guest shim via NYRQIS_AARCH64_CC: $NYRQIS_AARCH64_CC"
        if $NYRQIS_AARCH64_CC -O2 -fPIC -shared -o \
                "$GUEST_TMP/$SHIM_SO_NAME" "$SHIM_SRC" -ldl; then
            GUEST_SHIM="$GUEST_TMP/$SHIM_SO_NAME"
        fi
    else
        for Z in "${NYRQIS_ZIG:-zig}" /tmp/zig-toolchain/zig \
                 "$HOME/.local/opt/zig/zig" "$HOME/.local/bin/zig"; do
            command -v "$Z" >/dev/null 2>&1 || [[ -x "$Z" ]] || continue
            log "cross-compiling guest shim with zig: $Z"
            if "$Z" cc -target aarch64-linux-gnu -O2 -fPIC -shared -o \
                    "$GUEST_TMP/$SHIM_SO_NAME" "$SHIM_SRC" -ldl; then
                GUEST_SHIM="$GUEST_TMP/$SHIM_SO_NAME"
                break
            fi
            log "zig cross-compile failed with $Z; trying the next candidate"
        done
    fi
    [[ -n "$GUEST_SHIM" ]] || die "no aarch64 cross compiler available — the emulated chroot needs an AARCH64 preload shim (an amd64 .so cannot load into arm64 processes)
  Fixes (all rootless):
    - zig: download the static tarball from https://ziglang.org/download
      and keep its 'zig' binary at ~/.local/opt/zig/zig (survives /tmp
      cleanup) or on PATH — or point NYRQIS_ZIG at it,
    - or set NYRQIS_AARCH64_CC='aarch64-linux-gnu-gcc' (plus -target
      aarch64 for clang-style drivers; plain gcc cross toolchains need
      no -target)."
    log "guest shim ready: $GUEST_SHIM"
    # NOTE: the HOST-side /tmp copy stays HOST-class — it is loaded only
    # by the chroot binary itself (which runs on the host). The GUEST
    # class lives at the same path INSIDE the target (staged by acquire
    # below and re-staged by the wrapper), so each loader resolves the
    # class it can actually load.
fi

# ---------------------------------------------------------------- handoff
# The shim must be visible at the SAME absolute path inside every chroot
# the builder performs. Bind-mounting host paths into the namespace is
# EPERM (mounts owned by the initial user namespace cannot be rebound
# from a child userns), so two mechanisms cooperate:
#   1. a pre-staged copy of the .so in the target's /tmp (debootstrap's
#      internal chroot calls do not inherit this shell's PATH);
#   2. a generated `chroot` wrapper earlier in PATH that stages the
#      correct-class .so into the target and re-execs the REAL chroot
#      preloading ONLY that in-target path (builder steps like the apt
#      top-up, useradd, mkinitramfs).
# The wrapper is BEST-EFFORT, not load-bearing: newer Ubuntu debootstrap
# sanitizes PATH, so its internal chroots may resolve the real
# /usr/sbin/chroot and skip the wrapper entirely. That case is covered
# by the CANONICAL preload path (/tmp/rootless-syscall-shim.so): the
# driver stages a host-class copy host-side and acquire pre-stages the
# correct-class copy in-target, so the same path resolves on both sides
# of the boundary without the wrapper. A foreign-class preload or a
# missing-side copy makes loaders warn — and noise fails honest
# captured-stderr gates (observed: dpkg --audit on a clean rootfs).
SHIMBIN="$(mktemp -d "$WORKROOT/nyrqis-shimbin.XXXXXX")"
cat > "$SHIMBIN/chroot" <<WRAPPER
#!/bin/sh
# Rootless-build chroot wrapper (generated by build-live-iso-rootless.sh):
# stage the preload shim into the target tree, then exec the real chroot.
# ONE shim path in the target (\$tgt/tmp/$SHIM_SO_NAME — exactly what the
# inherited LD_PRELOAD names): the GUEST-class (aarch64) shim when cross-
# building, since EVERY chrooted process is emulated aarch64 then; the
# host-class shim otherwise.
SHIM_SO="$SHIM_SO"
GUEST_SHIM="$GUEST_SHIM"
SHIM_SO_NAME="$SHIM_SO_NAME"
QEMU_STATIC="$QEMU_STATIC"
tgt=""
for a in "\$@"; do
    case "\$a" in
        -*) continue ;;
        *)  if [ -d "\$a" ] && [ "\$a" != "/" ]; then tgt="\$a"; break; fi ;;
    esac
done
if [ -n "\$tgt" ]; then
    # Atomic staging (helper, tmp+mv) — NEVER cp -f onto the live
    # preload path: the in-target copy may be mapped by chrooted
    # processes, and cp -f's truncate executes zeros under them.
    if [ -n "\$GUEST_SHIM" ] && [ -f "\$GUEST_SHIM" ]; then
        "$STAGE_SHIM" "\$GUEST_SHIM" "\$tgt/tmp/\$SHIM_SO_NAME" 2>/dev/null || :
        # Keep the emulated binary staged at the registered binfmt path
        # (binfmt resolves the interpreter INSIDE the chroot).
        if [ -x "/usr/bin/\$QEMU_STATIC" ] && [ ! -x "\$tgt/usr/bin/\$QEMU_STATIC" ]; then
            mkdir -p "\$tgt/usr/bin" 2>/dev/null
            cp -f "/usr/bin/\$QEMU_STATIC" "\$tgt/usr/bin/" 2>/dev/null || :
        fi
    elif [ -f "\$SHIM_SO" ]; then
        "$STAGE_SHIM" "\$SHIM_SO" "\$tgt/tmp/\$SHIM_SO_NAME" 2>/dev/null || :
    fi
fi
# Host-side re-stage: atomic (helper) — THIS shell and the builder bash
# are mapped from exactly this file; a truncating overwrite hands them
# zeros (observed as cp+bash SIGSEGV cores mid-build).
"$STAGE_SHIM" "\$SHIM_SO" "/tmp/\$SHIM_SO_NAME" 2>/dev/null || :
exec env LD_PRELOAD="/tmp/$SHIM_SO_NAME" /usr/sbin/chroot "\$@"
WRAPPER
chmod 0755 "$SHIMBIN/chroot"

# ------------------------------------------------------------ acquisition-only
# --acquire-rootfs DIR: run ONLY the debootstrap step into DIR and exit.
# The builder's --rootfs path then consumes it. Splits the long
# download+install phase from the (fast) ISO assembly so each fits a
# bounded foreground runtime window; resumable via the cache dir.
if [[ -n "$ACQUIRE_ROOTFS" ]]; then
    SUITE="${NYRQIS_LIVE_SUITE:-bookworm}"
    MIRROR="${NYRQIS_LIVE_MIRROR:-http://deb.debian.org/debian}"
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
    # Pre-stage under the CANONICAL preload name: the outer LD_PRELOAD is
    # /tmp/rootless-syscall-shim.so, so this copy — not the raw mktemp
    # name — is what every chrooted loader resolves (a plain copy once
    # left only the mktemp-named file in the target and the runner's
    # debootstrap ran its core install UNSHIMMED: chown /var/mail →
    # EINVAL). amd64: host class; arm64: the guest shim below overwrites
    # it with the aarch64 class. (Fresh target — no process maps this
    # yet — so a plain copy is safe here.)
    cp -f "$SHIM_SO" "$ACQUIRE_ROOTFS/tmp/$SHIM_SO_NAME"
    if [[ -n "$GUEST_SHIM" ]]; then
        # Cross build: the emulated processes must find the GUEST-class
        # shim at the preloaded path (an amd64 .so is rejected by the
        # aarch64 loader).
        cp -f "$GUEST_SHIM" "$ACQUIRE_ROOTFS/tmp/$SHIM_SO_NAME"
        mkdir -p "$ACQUIRE_ROOTFS/usr/bin"
        cp -f "/usr/bin/$QEMU_STATIC" "$ACQUIRE_ROOTFS/usr/bin/"
    fi
    # Direct debootstrap call — deliberately NOT the builder: acquisition
    # alone cannot pass the builder's chroot gates, and this phase needs
    # the cache-dir resume story (a --cache-dir OUTSIDE the target
    # survives the wipe-and-retry above). Drift between this include
    # list and the builder's is harmless BY CONSTRUCTION: the builder's
    # ensure-packages step installs missing probe packages on the
    # --rootfs path, and its dpkg-audit + probe-parity gates fail the
    # build if anything is still absent.
    stage_watchdog & WATCHDOG_PID=$!
    set +e
    unshare -Urmpf --mount-proc env \
        LD_PRELOAD="/tmp/$SHIM_SO_NAME" \
        PATH="$SHIMBIN:$PATH" \
        debootstrap --variant=minbase --arch="$DEB_ARCH" \
            "${FOREIGN[@]}" \
            --cache-dir="$CACHE" \
            --include=systemd,systemd-sysv,sudo,$KERNEL_PKG,live-boot,live-boot-initramfs-tools,python3,python3-zstandard,python3-cffi,python3-ply,python3-nacl,python3-lz4,fuse3 \
            "$SUITE" "$ACQUIRE_ROOTFS" "$MIRROR"
    RC=$?
    set -e
    # Foreign (arm64) stage 2: chrooted, therefore EMULATED. The chroot
    # goes through the wrapper (PATH), which re-stages the guest shim +
    # emulator and RE-EXECS chroot with ONLY the in-target preload path
    # (the class boundary — a foreign-class .so in LD_PRELOAD makes every
    # chrooted loader print a warning, which pollutes captured stderr and
    # tripped the builder's dpkg-audit gate on an otherwise clean rootfs).
    if [ "$RC" -eq 0 ] && ((${#FOREIGN[@]})); then
        log "second-stage debootstrap under $QEMU_STATIC (emulated, rootless)"
        set +e
        unshare -Urmpf --mount-proc env \
            LD_PRELOAD="/tmp/$SHIM_SO_NAME" \
            PATH="$SHIMBIN:$PATH" \
            chroot "$ACQUIRE_ROOTFS" /debootstrap/debootstrap --second-stage
        RC=$?
        set -e
    fi
    if [ "$RC" -eq 0 ]; then
        touch "$STAMP"
        log "acquisition complete (stamp written)"
    fi
    exit "$RC"
fi

stage_watchdog & WATCHDOG_PID=$!

# ---------------------------------------------------------------- build
log "launching $BUILDER_NAME in a user namespace (no root involved)"
log "extra builder args: ${POSITIONAL[*]:-(none)}"

set +e
BUILDER_ARGS=()
[[ -n "$OUTPUT" ]] && BUILDER_ARGS+=(--output "$OUTPUT")
[[ -n "$ARCH" ]] && BUILDER_ARGS+=(--arch "$ARCH")
# Cross-building (host arch != target arch): tell the builder to skip
# Rust artifacts of the HOST arch — they are inert in a foreign image
# and only bloat it (the demo's loader honestly reports unloaded).
case "$(uname -m):$ARCH" in
    x86_64:arm64) CDYLIB_ARCH=aarch64 ;;
    aarch64:amd64) CDYLIB_ARCH=amd64 ;;
    *) CDYLIB_ARCH="" ;;
esac
# The builder's ISO-assembly workdir defaults to /tmp, which
# systemd-tmpfiles may EMPTY mid-build (see WORKROOT): default it into
# the durable workroot unless the caller passed --workdir explicitly.
HAVE_WORKDIR=false
for a in "${POSITIONAL[@]:-}"; do [[ "$a" == "--workdir" ]] && HAVE_WORKDIR=true; done
$HAVE_WORKDIR || BUILDER_ARGS+=(--workdir "$WORKROOT/build-$$")
# The outer LD_PRELOAD is the CANONICAL path (/tmp/$SHIM_SO_NAME):
# host-side it resolves to the driver's host-class copy; inside any
# chroot, to the target's own copy (correct class per side — guest on a
# cross build). Chrooted shimming therefore does NOT depend on the
# chroot wrapper engaging, which a PATH-sanitizing debootstrap can skip.
unshare -Urmpf --mount-proc env \
    LD_PRELOAD="/tmp/$SHIM_SO_NAME" \
    ${CDYLIB_ARCH:+NYRQIS_CDYLIB_ARCH="$CDYLIB_ARCH"} \
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
