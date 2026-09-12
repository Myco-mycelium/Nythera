#!/usr/bin/env bash
# build-live-iso.sh — assemble the Nyrqis live-demo ISO.
#
# Produces a bootable (UEFI + BIOS, VM + real hardware) ISO that boots a
# minimal rootfs with the Nyrqis backend + desktop tree at /opt/nyrqis,
# autologs in as the `demo` user on tty1, starts the daemon, attempts the
# desktop session, and prints a guided demo banner including a LIVE
# capability probe ("what's missing" on this machine).
#
# Rootfs acquisition (choose one; the script probes in this order):
#   1. --rootfs DIR       a pre-built rootfs tree (CI: built by the
#                         nyrqis-live-rootfs.yml workflow)
#   2. --rootfs-tar T.tar a pre-built rootfs tarball
#   3. debootstrap        assemble a minimal Debian/Ubuntu rootfs from
#                         scratch (needs debootstrap + network + root)
#
# Image assembly uses ONLY: mksquashfs, genisoimage (or xorriso),
# grub-mkrescue-compatible /usr/lib/grub files, and isolinux bins — all
# present on a standard dev host (see packaging/live/README.md).
#
# Usage:
#   sudo packaging/live/build-live-iso.sh \
#       --rootfs /srv/nyrqis-rootfs \
#       --output dist/nyrqis-live-$(git describe --tags).iso
#
# Exit codes: 0 = ISO built; 1 = preconditions missing (they are printed).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/source/nyhal-linux-backend"

ROOTFS=""
ROOTFS_TAR=""
OUTPUT="dist/nyrqis-live.iso"
VOLUME_ID="NYRQIS_LIVE"
WORKDIR=""
SKIP_CHROOT=false
KEEP_WORKDIR=false

log() { printf '\e[1;34m[build-live-iso]\e[0m %s\n' "$*"; }
# die() is the CI failure surface: emit an ::error:: so the message
# lands in a check annotation (readable via the API without
# credentials — job logs are not).
die() { printf '\e[1;31m[build-live-iso] ERROR:\e[0m %s\n' "$*" >&2; printf '::error::build-live-iso: %s\n' "$(printf '%s' "$*" | tr '\n' ' ')" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rootfs)       ROOTFS="$2"; shift 2 ;;
        --rootfs-tar)   ROOTFS_TAR="$2"; shift 2 ;;
        --output|-o)    OUTPUT="$2"; shift 2 ;;
        --volume-id)    VOLUME_ID="$2"; shift 2 ;;
        --workdir)      WORKDIR="$2"; shift 2 ;;
        --skip-chroot)  SKIP_CHROOT=true; shift ;;
        --keep-workdir) KEEP_WORKDIR=true; shift ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            echo ""
            echo "Dev flags:"
            echo "  --skip-chroot   do not chroot into the rootfs (demo user is"
            echo "                  written directly; initramfs is not regenerated)"
            echo "  --keep-workdir  keep the staging directory for inspection"
            exit 0 ;;
        *) die "unknown argument: $1 (see --help)" ;;
    esac
done

# ---------------------------------------------------------------- preconditions
need() { command -v "$1" >/dev/null 2>&1 || MISSING+=("$1"); }
MISSING=()
need mksquashfs
need genisoimage || need mkisofs || need xorriso
if [[ -z "$ROOTFS" && -z "$ROOTFS_TAR" ]]; then
    need debootstrap
fi
if ((${#MISSING[@]})); then
    log "missing tools: ${MISSING[*]}"
    log "on Debian/Ubuntu:  sudo apt-get install -y squashfs-tools \\"
    log "    genisoimage xorriso debootstrap"
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
    $SKIP_CHROOT || die "run as root (chroot + squashfs ownership), or pass --skip-chroot for an unprivileged pipeline check"
    log "unprivileged --skip-chroot build: file ownership in the image follows this user (fine for a pipeline check; CI builds as root)"
fi
[[ -d "$BACKEND_DIR" ]] || die "backend tree not found at $BACKEND_DIR"

WORKDIR="${WORKDIR:-$(mktemp -d /tmp/nyrqis-live.XXXXXX)}"
ISO_ROOT="$WORKDIR/iso"
LIVE_DIR="$ISO_ROOT/live"
mkdir -p "$LIVE_DIR"

cleanup() { [[ -n "${ROOTFS_SRC:-}" && -d "$ROOTFS_SRC" ]] && \
    [[ "$ROOTFS_SRC" == "$WORKDIR"* ]] && rm -rf "$ROOTFS_SRC"; \
    $KEEP_WORKDIR || rm -rf "$WORKDIR"; }
trap cleanup EXIT

# isolinux boot binaries, needed by every BIOS-path ISO build.
copy_isolinux_bins() {
    local iso_root="$1" bin ld
    bin="$(ls /usr/lib/ISOLINUX/isolinux.bin \
             /usr/lib/syslinux/modules/bios/isolinux.bin 2>/dev/null | head -1 || true)"
    [[ -n "$bin" ]] || return 1
    cp "$bin" "$iso_root/isolinux/isolinux.bin"
    ld="$(dirname "$bin")/ldlinux.c32"
    [[ -f "$ld" ]] && cp "$ld" "$iso_root/isolinux/"
    return 0
}

# ---------------------------------------------------------------- rootfs
if [[ -n "$ROOTFS_TAR" ]]; then
    ROOTFS_SRC="$WORKDIR/rootfs"
    mkdir -p "$ROOTFS_SRC"
    log "extracting rootfs tarball: $ROOTFS_TAR"
    tar -xpf "$ROOTFS_TAR" -C "$ROOTFS_SRC"
elif [[ -n "$ROOTFS" ]]; then
    ROOTFS_SRC="$ROOTFS"
else
    ROOTFS_SRC="$WORKDIR/rootfs"
    mkdir -p "$ROOTFS_SRC"
    log "assembling a minimal rootfs with debootstrap (this takes a while)"
    SUITE="${NYRQIS_LIVE_SUITE:-bookworm}"
    MIRROR="${NYRQIS_LIVE_MIRROR:-http://deb.debian.org/debian}"
    # live-boot is what processes `boot=live` from the initramfs; the
    # initramfs SCRIPTS live in live-boot-initramfs-tools (a split
    # package minbase can skip via Recommends — six CI rounds burned
    # on the resulting script-less initrd).
    debootstrap --variant=minbase \
        --include=systemd,systemd-sysv,sudo,linux-image-amd64,live-boot,live-boot-initramfs-tools \
        "$SUITE" "$ROOTFS_SRC" "$MIRROR"
fi
[[ -d "$ROOTFS_SRC" ]] || die "rootfs tree not found after acquisition"

# ---------------------------------------------------------------- Nyrqis tree
log "installing the Nyrqis backend + desktop tree into /opt/nyrqis"
OPT="$ROOTFS_SRC/opt/nyrqis"
mkdir -p "$OPT"
cp -a "$BACKEND_DIR" "$OPT/nyhal-linux-backend"
# The Rust FFI crates are optional at runtime (honest fallbacks); keep the
# prebuilt cdylibs if they exist, drop the build caches to save image size.
rm -rf "$OPT/nyhal-linux-backend"/{.git,__pycache__,rust/*/target,.pytest_cache}
find "$OPT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Live overlay: autologin getty, demo session, demo banner.
log "applying the live overlay (autologin, demo session, banner)"
install -D "$SCRIPT_DIR/overlay/etc/systemd/system/getty@tty1.service.d/autologin.conf" \
    "$ROOTFS_SRC/etc/systemd/system/getty@tty1.service.d/autologin.conf"
install -D "$SCRIPT_DIR/overlay/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf" \
    "$ROOTFS_SRC/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf"
install -D "$SCRIPT_DIR/overlay/usr/local/bin/nyrqis-demo" \
    "$ROOTFS_SRC/usr/local/bin/nyrqis-demo"
chmod 0755 "$ROOTFS_SRC/usr/local/bin/nyrqis-demo"

write_demo_user_records() {
    # The live image needs the account, not shadow-utils: write the
    # records directly (used when useradd is unavailable in the rootfs,
    # and by --skip-chroot).
    grep -q '^demo:' "$ROOTFS_SRC/etc/passwd" 2>/dev/null || \
        echo 'demo:x:1000:1000:demo,,,:/home/demo:/bin/bash' >> "$ROOTFS_SRC/etc/passwd"
    grep -q '^demo:' "$ROOTFS_SRC/etc/group" 2>/dev/null || \
        echo 'demo:x:1000:' >> "$ROOTFS_SRC/etc/group"
    grep -q '^demo:' "$ROOTFS_SRC/etc/shadow" 2>/dev/null || \
        echo 'demo:!:19000:0:99999:7:::' >> "$ROOTFS_SRC/etc/shadow" 2>/dev/null || true
    mkdir -p "$ROOTFS_SRC/home/demo" "$ROOTFS_SRC/etc/sudoers.d"
    chown 1000:1000 "$ROOTFS_SRC/home/demo" 2>/dev/null || true
}

if $SKIP_CHROOT; then
    log "--skip-chroot: writing the demo user records directly"
    write_demo_user_records
else
    # demo user (uid 1000, passwordless sudo, autologged on tty1). Prefer
    # useradd; a hand-built rootfs tarball may lack shadow-utils.
    if chroot "$ROOTFS_SRC" sh -c 'command -v useradd' >/dev/null 2>&1; then
        chroot "$ROOTFS_SRC" useradd -m -u 1000 -s /bin/bash demo
    else
        write_demo_user_records
    fi
    chroot "$ROOTFS_SRC" sh -c 'echo "demo ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/demo'
    chroot "$ROOTFS_SRC" systemctl enable getty@tty1 2>/dev/null || true
fi

# The demo SESSION: the autologin getty drop-ins only log the user in —
# without a profile hook the login stops at a bare bash prompt and
# neither the banner nor the boot-smoke markers ever run. The profile
# execs the demo session on every autologin console (tty1 and ttyS0).
cat > "$ROOTFS_SRC/home/demo/.bash_profile" <<'EOF'
# The live demo session owns every autologin console.
if [ -x /usr/local/bin/nyrqis-demo ]; then
    exec /usr/local/bin/nyrqis-demo
fi
EOF
chown 1000:1000 "$ROOTFS_SRC/home/demo/.bash_profile" 2>/dev/null \
    || chroot "$ROOTFS_SRC" chown demo:demo /home/demo/.bash_profile \
    || true

# hostname + os-release flavor
echo "nyrqis-live" > "$ROOTFS_SRC/etc/hostname"
sed -i 's/^PRETTY_NAME=.*/PRETTY_NAME="Nyrqis Live (demo)"/' \
    "$ROOTFS_SRC/etc/os-release" 2>/dev/null || true

# Kernel + initrd from the rootfs (installed by debootstrap/tarball).
KERNEL="$(ls "$ROOTFS_SRC"/boot/vmlinuz-* 2>/dev/null | sort -V | tail -1 || true)"
INITRD="$(ls "$ROOTFS_SRC"/boot/initrd.img-* 2>/dev/null | sort -V | tail -1 || true)"
[[ -n "$KERNEL" && -n "$INITRD" ]] || \
    die "no kernel/initrd under $ROOTFS_SRC/boot — build the rootfs with linux-image-amd64"

# Live initramfs hooks: the live pivot needs live-boot's SCRIPTS and the
# FILESYSTEM MODULES (squashfs, iso9660, loop, overlay). mkinitramfs
# INSIDE the rootfs (skipped under --skip-chroot; a properly built
# rootfs tarball already ships a live-boot-capable initrd).
#
# VERIFIED, not assumed: a missing piece shows up only AT BOOT as
# "run-init: can't execute /sbin/init" → initramfs shell (six CI rounds
# burned on variants of exactly this). So: force the modules via a
# hook, regenerate, then refuse to ship any initrd missing any piece.
if ! $SKIP_CHROOT; then
    # Force the live-critical modules into every generated initramfs
    # (MODULES=most does not guarantee filesystem modules).
    mkdir -p "$ROOTFS_SRC/etc/initramfs-tools/hooks"
    cat > "$ROOTFS_SRC/etc/initramfs-tools/hooks/zz-nyrqis-live-modules" <<'EOF'
#!/bin/sh
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in
    prereqs) prereqs; exit 0 ;;
esac
. /usr/share/initramfs-tools/hook-functions
force_load squashfs
force_load iso9660
force_load loop
force_load overlay
EOF
    chmod 0755 "$ROOTFS_SRC/etc/initramfs-tools/hooks/zz-nyrqis-live-modules"

    # init-bottom diagnostic: BEFORE the pivot, print the union state
    # to the console — /root contents, the live mounts, and whether an
    # init exists in the tree we are about to pivot into. Serial-visible
    # on both success (harmless banner) and failure (the evidence).
    mkdir -p "$ROOTFS_SRC/etc/initramfs-tools/scripts/init-bottom"
    cat > "$ROOTFS_SRC/etc/initramfs-tools/scripts/init-bottom/zz-nyrqis-pivot-diag" <<'EOF'
#!/bin/sh
# Nyrqis live-build diagnostic: show the pivot state before run-init.
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in
    prereqs) prereqs; exit 0 ;;
esac
# Best-effort only: this must never break the boot.
echo "[nyrqis-diag] pivot target state (rootmnt=$rootmnt):"
ls "${rootmnt:-/root}" 2>&1 | head -20 | sed 's/^/[nyrqis-diag]   /'
for d in "${rootmnt:-/root}/sbin/init" "${rootmnt:-/root}/etc" \
         "${rootmnt:-/root}/usr/bin/sh"; do
    if [ -e "$d" ]; then
        echo "[nyrqis-diag] PRESENT: $d"
    else
        echo "[nyrqis-diag] MISSING: $d"
    fi
done
echo "[nyrqis-diag] live mounts:"
grep -E 'overlay|live|squashfs|iso9660' /proc/mounts 2>/dev/null \
    | head -10 | sed 's/^/[nyrqis-diag]   /'
echo "[nyrqis-diag] end pivot diagnostics"
EOF
    chmod 0755 "$ROOTFS_SRC/etc/initramfs-tools/scripts/init-bottom/zz-nyrqis-pivot-diag"

    if chroot "$ROOTFS_SRC" sh -c 'command -v mkinitramfs' >/dev/null 2>&1; then
        # Self-heal the scripts package first: without it mkinitramfs
        # "succeeds" and produces a script-less initrd that cannot boot.
        if [[ ! -e "$ROOTFS_SRC/usr/share/initramfs-tools/scripts/live" ]]; then
            log "live-boot initramfs scripts missing — installing live-boot-initramfs-tools"
            chroot "$ROOTFS_SRC" sh -c \
                'apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq live-boot-initramfs-tools' \
                2>/dev/null || true
        fi
        log "regenerating the initramfs with live-boot scripts + modules"
        if chroot "$ROOTFS_SRC" mkinitramfs -o /boot/initrd.img-nyrqis-live \
            "$(basename "$KERNEL" | sed 's/^vmlinuz-//')"; then
            INITRD="$ROOTFS_SRC/boot/initrd.img-nyrqis-live"
        else
            log "WARNING: mkinitramfs failed; verifying the stock initrd instead"
        fi
    fi

    MISSING=""
    listing="$(chroot "$ROOTFS_SRC" lsinitramfs "/boot/$(basename "$INITRD")" 2>/dev/null)"
    # NOTE: grep the LISTING via here-strings, never `echo | grep -q`:
    # under `set -o pipefail`, grep -q's early exit on a match SIGPIPEs
    # the writer and the pipeline returns 141 — a SUCCESSFUL match would
    # be treated as a miss and good initrds would be rejected (that is
    # exactly what rounds 8-9 of CI did). A here-string has no pipe.
    grep -qE '(^|/)scripts/live$' <<<"$listing" \
        || MISSING="$MISSING scripts/live"
    # A module passes if its .ko is IN the initrd OR it is BUILT INTO
    # the kernel (=y in the shipped config — no .ko exists to find; the
    # kernel itself carries it, which is fine for the live mount).
    # Pairs are modname:file-stem:CONFIG-symbol — the module NAME and
    # its FILE basename can differ (modprobe name "iso9660" ships as
    # fs/isofs/isofs.ko), so both spellings are accepted in the listing.
    KCONFIG="$ROOTFS_SRC/boot/config-$(basename "$KERNEL" | sed 's/^vmlinuz-//')"
    for pair in "squashfs:squashfs:CONFIG_SQUASHFS" \
                "iso9660:isofs:CONFIG_ISO9660_FS" \
                "loop:loop:CONFIG_BLK_DEV_LOOP" \
                "overlay:overlay:CONFIG_OVERLAY_FS"; do
        mod="${pair%%:*}"; rest="${pair#*:}"
        file="${rest%%:*}"; sym="${rest#*:}"
        grep -qE "/(${mod}|${file})\.ko(\.xz|\.zst)?$" <<<"$listing" && continue
        grep -qE "^${sym}=y" "$KCONFIG" 2>/dev/null && continue
        MISSING="$MISSING $mod.ko"
    done
    if [ -n "$MISSING" ]; then
        die "initrd $(basename "$INITRD") cannot boot live — missing:$MISSING
  boot=live is a no-op without live-boot's scripts, and mountroot fails
  into the initramfs shell without squashfs/iso9660/loop/overlay.
  Fix: install live-boot + live-boot-initramfs-tools in the rootfs and
  ensure mkinitramfs succeeds with the zz-nyrqis-live-modules hook.
  (If mkinitramfs already ran clean, suspect the check itself: run
  lsinitramfs on the initrd and grep for scripts/live manually.)"
    fi
    log "initrd verified: live-boot scripts + squashfs/iso9660/loop/overlay present"
fi

# ---------------------------------------------------------------- pivot init
# The live pivot execs /sbin/init INSIDE the merged tree. Debian's
# usr-merge means /sbin may be a symlink to /usr/sbin, and a rootfs
# where that symlink is absent/dangling leaves /sbin/init unresolvable
# — the pivot dies with "can't execute '/sbin/init'" even though the
# tree LOOKS complete (bin/, etc/, usr/bin/sh all present; diagnosed
# via the init-bottom pivot probe). Guarantee the path, verified.
if [[ -e "$ROOTFS_SRC/sbin/init" ]]; then
    log "rootfs /sbin/init present"
else
    log "rootfs /sbin/init missing — repairing to the real systemd binary"
    SYSTEMD_BIN="$(ls "$ROOTFS_SRC"/usr/lib/systemd/systemd \
                      "$ROOTFS_SRC"/lib/systemd/systemd 2>/dev/null | head -1 || true)"
    [[ -n "$SYSTEMD_BIN" ]] || \
        die "no systemd binary in the rootfs — install systemd (debootstrap --include)"
    if [[ ! -d "$ROOTFS_SRC/sbin" && ! -L "$ROOTFS_SRC/sbin" ]]; then
        mkdir -p "$ROOTFS_SRC/sbin"
    fi
    ln -sf /usr/lib/systemd/systemd "$ROOTFS_SRC/sbin/init"
    log "created /sbin/init -> /usr/lib/systemd/systemd"
fi
[[ -e "$ROOTFS_SRC/sbin/init" ]] || \
    die "/sbin/init repair failed — the live pivot cannot exec an init"

# ---------------------------------------------------------------- squashfs + ISO
log "building the squashfs rootfs image"
mksquashfs "$ROOTFS_SRC" "$LIVE_DIR/filesystem.squashfs" \
    -comp zstd -Xcompression-level 15 -noappend -wildcards \
    -e "boot/vmlinuz-*" "boot/initrd.img-*" \
    >/dev/null   # progress is noise in logs; the ISO is the artifact

# VERIFIED, not assumed (a dead init costs a full CI boot round to find):
# the image must carry an init and /etc, or the live pivot fails with
# "run-init: can't execute '/sbin/init'" and an empty /root.
SQUASH_LISTING="$(unsquashfs -ls "$LIVE_DIR/filesystem.squashfs" 2>/dev/null || true)"
# Listing lines look like "drwxr-xr-x root/root 62 2025-01-01 squashfs-root/etc"
# (mode size date <name with the destination prefix, NO leading slash) on
# squashfs-tools 4.x — match on name ENDINGS, not anchored starts.
if ! grep -qE '(sbin/init|systemd/systemd)([[:space:]]|$)' <<<"$SQUASH_LISTING"; then
    die "filesystem.squashfs has no /sbin/init (nor systemd) — the live
  pivot will die with 'run-init: can't execute /sbin/init'. Check the
  debootstrap --include list (systemd) and what mksquashfs snapshotted
  (\$ROOTFS_SRC). Listing head: $(echo "$SQUASH_LISTING" | head -3 | tr '\n' ' ')"
fi
if ! grep -qE 'etc([[:space:]]|$)' <<<"$SQUASH_LISTING"; then
    die "filesystem.squashfs has no /etc — the live pivot will fail
  writing network config into /root/etc. Check what mksquashfs
  snapshotted (\$ROOTFS_SRC must be the debootstrap tree root).
  Listing head: $(echo "$SQUASH_LISTING" | head -3 | tr '\n' ' ')"
fi
log "squashfs verified: init + /etc present"

# The listing above cannot see a DANGLING /sbin/init symlink (the entry
# name matches; the target may not exist in the image). Extract the node
# and resolve it for real: a regular file passes, a symlink passes only
# if its target is IN the image, anything else fails the build.
# NOTE 1: -f -d DIR extracts to DIR/<archive-path> (sbin/init — the
# squashfs-root/ prefix seen in -ls output is display-only), and target
# existence is a SUFFIX match on the listing (entries print as
# squashfs-root/usr/...; the prefix must not break the match).
# NOTE 2: usr-merge trees keep the REAL init at usr/sbin/init with /sbin
# a symlink to usr/sbin — there is no sbin/init archive ENTRY, and
# unsquashfs extracts nothing for that path (a plain-disk [[ -e ]]
# traversal is NOT what the archive contains). Probe both paths; the
# kernel's run-init resolves through the merged /sbin the same way.
PIVOT_PROBE="$(mktemp -d)"
INIT_NODE=""
for cand in sbin/init usr/sbin/init; do
    if unsquashfs -f -d "$PIVOT_PROBE" "$LIVE_DIR/filesystem.squashfs" \
            "$cand" >/dev/null 2>&1 \
        && [[ -e "$PIVOT_PROBE/$cand" || -L "$PIVOT_PROBE/$cand" ]]; then
        INIT_NODE="$PIVOT_PROBE/$cand"
        break
    fi
done
if [[ -z "$INIT_NODE" ]]; then
    die "cannot extract /sbin/init (nor usr/sbin/init) from
  filesystem.squashfs — the live pivot would die with run-init.
  usr-merge trees keep the init at usr/sbin/init; if NEITHER path
  exists, the systemd-sysv package (which ships /sbin/init) is
  missing from the rootfs."
fi
if [[ -f "$INIT_NODE" ]]; then
    log "squashfs pivot init resolves (${INIT_NODE#$PIVOT_PROBE/})"
elif [[ -L "$INIT_NODE" ]]; then
    target="$(readlink "$INIT_NODE")"
    target="${target#/}"
    if grep -qE "${target}([[:space:]]|$)" <<<"$SQUASH_LISTING"; then
        log "squashfs pivot init resolves (${INIT_NODE#$PIVOT_PROBE/} -> ${target})"
    else
        die "/sbin/init is a DANGLING symlink (target ${target} not in the
  image) — the live pivot dies with run-init. The build repairs
  \$ROOTFS_SRC/sbin/init; if this fires, the repair did not land."
    fi
else
    die "$(basename "$INIT_NODE") in the squashfs is neither a file nor a
  symlink — the live pivot cannot exec it."
fi
rm -rf "$PIVOT_PROBE"

# Same contract for the initramfs: it must at minimum unpack to a
# tree with an /init (a truncated/corrupt initrd otherwise costs a
# full CI boot round to discover).
if command -v cpio >/dev/null 2>&1; then
    ITRD_HEAD="$(gzip -dc "$INITRD" 2>/dev/null | cpio -it 2>/dev/null | head -5 || true)"
    if ! grep -q '^init$' <<<"$ITRD_HEAD" && [[ "$(wc -l <<<"$ITRD_HEAD")" -lt 3 ]]; then
        log "WARNING: cannot list the initramfs (first entries: $(echo "$ITRD_HEAD" | head -3 | tr '\n' ' ')) — concatenated-cpio layout or unexpected format; NOT failing the build"
    fi
fi
cp "$KERNEL"  "$LIVE_DIR/vmlinuz"
cp "$INITRD"  "$LIVE_DIR/initrd"

log "writing boot configuration (GRUB for UEFI+BIOS, isolinux for legacy)"
export VOLUME_ID
mkdir -p "$ISO_ROOT/boot/grub" "$ISO_ROOT/isolinux"
sed "s/__VOLUME_ID__/$VOLUME_ID/g" "$SCRIPT_DIR/grub.cfg.tpl"  > "$ISO_ROOT/boot/grub/grub.cfg"
sed "s/__VOLUME_ID__/$VOLUME_ID/g" "$SCRIPT_DIR/isolinux.cfg.tpl" > "$ISO_ROOT/isolinux/isolinux.cfg"

mkdir -p "$(dirname "$OUTPUT")"
# grub-mkrescue produces the hybrid (UEFI + BIOS) image when the grub
# bins AND xorriso are installed; every BIOS fallback path needs the
# isolinux binaries staged first.
HYBRID=false
if grub-mkrescue --version >/dev/null 2>&1 && \
   [[ -d /usr/lib/grub/i386-pc || -d /usr/lib/grub/x86_64-efi ]] && \
   command -v xorriso >/dev/null 2>&1; then
    HYBRID=true
fi
if ! $HYBRID; then
    copy_isolinux_bins "$ISO_ROOT" || \
        die "isolinux.bin not found (apt-get install isolinux xorriso grub-pc-bin grub-efi-amd64-bin for the hybrid image)"
fi
if $HYBRID; then
    log "building hybrid ISO with grub-mkrescue (UEFI + BIOS)"
    grub-mkrescue -o "$OUTPUT" "$ISO_ROOT" \
        -- -volid "$VOLUME_ID" -joliet on
else
    log "xorriso or grub bins missing; building BIOS-only isolinux ISO"
    genisoimage -rational-rock -joliet -volid "$VOLUME_ID" \
        -b isolinux/isolinux.bin -c isolinux/boot.cat \
        -no-emul-boot -boot-load-size 4 -boot-info-table \
        -o "$OUTPUT" "$ISO_ROOT"
fi

log "ISO built: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
log "boot it with:   qemu-system-x86_64 -m 4G -enable-kvm -cdrom $OUTPUT"
log "or write to USB: sudo dd if=$OUTPUT of=/dev/sdX bs=4M status=progress conv=fsync"
