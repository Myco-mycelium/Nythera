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
rootfs + ISO on every push to `main`, **boots the ISO headless in QEMU**
and asserts the daemon answers ping (the serial handshake in
`tests/boot_smoke.py` — `NYRQIS_BOOT_SMOKE_PONG=1`), then uploads the
ISO and the serial log as artifacts — download both from the Actions
run page; no local build required.

Run the boot smoke yourself (needs `qemu-system-x86`):

```bash
python3 tests/boot_smoke.py dist/nyrqis-live.iso --timeout 600 --keep-logs
```
