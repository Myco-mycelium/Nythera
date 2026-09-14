# Nyrqis Live Demo ISO

A bootable ISO that starts a full Nyrqis demo on any machine — VM or real
hardware — with **zero installation**: backend daemon up, desktop session
attempted, and a capability probe that prints exactly **what is missing**
on the machine it booted.

```
packaging/live/
  build-live-iso.sh          # the builder (rootfs + squashfs + hybrid ISO)
  grub.cfg.tpl               # UEFI + BIOS GRUB menu (via grub-mkrescue)
  isolinux.cfg.tpl           # legacy BIOS fallback menu
  overlay/
    etc/systemd/system/getty@tty1.service.d/autologin.conf
    usr/local/bin/nyrqis-demo   # the demo session (banner + probe)
```

## Build

```bash
sudo apt-get install -y debootstrap squashfs-tools genisoimage xorriso \
    grub-pc-bin grub-efi-amd64-bin mtools isolinux
sudo packaging/live/build-live-iso.sh -o dist/nyrqis-live.iso
```

The default path assembles a minimal Debian bookworm rootfs with
debootstrap (`NYRQIS_LIVE_SUITE` / `NYRQIS_LIVE_MIRROR` to switch),
installs the backend + desktop tree at `/opt/nyrqis`, applies the live
overlay, and packs everything into a zstd squashfs.

Got a prebuilt rootfs (CI produces one)? Skip debootstrap:

```bash
sudo packaging/live/build-live-iso.sh --rootfs /srv/nyrqis-rootfs -o out.iso
# or
sudo packaging/live/build-live-iso.sh --rootfs-tar rootfs.tar.zst -o out.iso
```

A prebuilt rootfs tree must contain: a `/boot/vmlinuz-*` +
`/boot/initrd.img-*` pair, `live-boot`, and enough userland for
`python3` + `bash` (everything Nyrqis-specific is copied in by the
builder).

## Boot it

**VM** (no GPU passthrough needed — the compositor falls back honestly):

```bash
qemu-system-x86_64 -m 4G -enable-kvm -cdrom dist/nyrqis-live.iso -boot d
```

**Real hardware** (hybrid image, UEFI + BIOS):

```bash
sudo dd if=dist/nyrqis-live.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Boot menu: `demo` (default), `verbose` (full boot log), `RAM` (copy the
squashfs into RAM — fastest on machines with memory to spare).

### Architecture support (honest status)

The ISO is **amd64/x86_64 only**: the rootfs installs
`linux-image-amd64`, and the boot images are `grub-pc-bin` +
`grub-efi-amd64-bin` + isolinux. An arm64 build (Raspberry Pi 4/5,
ARM servers) is a Phase-2 roadmap item — the pieces short of it:

- `grub-efi-arm64-bin` + `grub-arm64-efi` (no isolinux on arm64 — UEFI
  only), `linux-image-arm64`, and an arm64 CI runner or `qemu-system-arm`
  for the boot smokes;
- the arm64 cdylibs themselves already cross-compile in CI
  (`arm64-conformance` workflow: 10 crates build for
  `aarch64-unknown-linux-gnu`, and the aarch64 seccomp table
  conformance passes) — the gap is the **image**, not the platform
code.

Do not hand-wave an arm64 ISO as "ready": it has never been built or
boot-smoked, and no release asset claims otherwise.

## What the demo does

The `demo` user is autologged on tty1 and `nyrqis-demo` runs:

1. **Daemon** — starts `nyrqis_backend.py service serve` unprivileged
   (the backend's user-namespace posture), then shows `ep-limits list`
   against the live socket.
2. **Desktop** — attempts `nyrqis_init.py` when a DRM device is usable;
   otherwise says so and hands you the command.
3. **Probe** — checks DRM nodes, FUSE, user namespaces, the Python
   modules (zstandard / PyNaCl / lz4), KVM, the prebuilt Rust crate, and
   prints `✗ MISSING:` lines for everything absent. That list is the
   input for "what is missing" — file it against the hardware matrix
   (M15 Phase 2).

Everything degrades honestly: a missing piece is a banner line, never a
crash or a forged success.

## CI

The `live-iso` workflow (`.github/workflows/live-iso.yml`) builds the
rootfs + ISO on every push to `main` and boots it **twice**:

1. **Direct boot** (`tests/boot_smoke.py`) — the ISO's kernel and
   initrd are extracted at run time (`xorriso`/`isoinfo`) and booted
   with `NYRQIS_BOOT_SMOKE=1` on a hand-built command line. Deliberately
   immune to bootloader problems: it isolates live-boot/daemon health.
2. **Menu-path boot** (`tests/boot_smoke_menu.py`, `menu-boot` job) —
   the ISO boots **like a real machine**: el torito → GRUB menu →
   default entry, no hand-holding. Asserts the demo session and daemon
   on the serial line. Every GRUB/isolinux menu entry carries
   `console=tty0 console=ttyS0,115200`, so the human boot path is
   serial-observable while the VGA console stays the primary surface.

Both upload the ISO and the serial logs as artifacts — download from
the Actions run page; no local build required. Dead boots (kernel
panic, missing live medium, initramfs rescue shell, GRUB "file not
found") fail fast, and every failure emits `::error::` annotations
carrying markers + the serial tail — readable through the API without
credentials. Budget per boot: 780 s (a TCG-slowed runner boots full
userspace 5–15× slower than KVM).

Run the boot smokes yourself (needs `qemu-system-x86`):

```bash
python3 tests/boot_smoke.py dist/nyrqis-live.iso --timeout 600 --keep-logs
python3 tests/boot_smoke_menu.py dist/nyrqis-live.iso --timeout 600 --keep-logs
```
