---
title: Boot the Live ISO
document_id: NHW-LIVE-ISO
version: 1.0.0
status: Draft
classification: How-to
owners:
  - Nyrqis Architecture
created: 2026-11-02
updated: 2026-11-02
ai_assisted: true
review_cycle: Continuous
---

# Boot the live ISO

The live disc boots the kernel from this repository with no disk writes:
GRUB loads `kernel.elf` through the Multiboot2 header, and the kernel picks
one of its four device modes. It is a **BIOS/CSM** medium by design — see
"why it is not UEFI-bootable" below.

## Build it

```bash
cd nykernel-package/source/nykernel
./tools/make_live_iso.sh            # builds the kernel, then the disc
./tools/make_live_iso.sh --no-build # package an existing target/kernel.elf
```

The script writes three files and prints their digests:

| file | what it is |
|---|---|
| `dist/nyrqis-live.iso` | the bootable image (GRUB El Torito, Multiboot2) |
| `dist/nyrqis-live.iso.sha256` | the image's digest, as recorded at build time |
| `dist/nyrqis-live.kernel.sha256` | the digest of the kernel the image carries |

## Check it

```bash
./tools/check_live_iso.sh          # integrity + attribution
./tools/check_live_iso.sh --boot   # ...plus a headless boot of the disc
```

Three separate facts, deliberately not one:

1. **integrity** — the ISO still hashes to its recorded digest;
2. **attribution** — the `kernel.elf` *inside* the ISO hashes to the
   recorded kernel digest, so a stale artifact fails instead of passing;
3. **behaviour** (`--boot`) — QEMU boots the disc headless and the kernel
   must report its device-mode verdict, bind `framebuffer` and `ps2-aux`,
   and verify the display by read-back.

## Run it

Write the image to a USB stick or attach it as a CD-ROM:

```bash
# QEMU, headless, serial console on stdout
qemu-system-x86_64 -cdrom dist/nyrqis-live.iso -m 512 -serial stdio

# or to a stick (destroys the stick's contents)
sudo dd if=dist/nyrqis-live.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

The GRUB menu has five entries: `desktop`, `tablet`, `phone`, `console`
(serial-friendly) and automatic, which lets the display class decide.

The mode is not decoration — each one reserves a different content pane, so
the Material shell inside it reflows for real:

- `desktop` — navigation rail, app bar, dock; the shell gets the widest pane
  and renders its `Expanded` layout;
- `tablet` — status strip, app bar, gesture pill; full-width pane;
- `phone` — the content is capped and centred (480 px), which drops the
  shell to its `Compact` layout with no quick-setting tiles;
- `console` — no shell at all (there is no pixel surface in a terminal
  session), a full-screen text surface instead.

Pass `nyrqis.mode=phne` (or any other typo) and the kernel **names** the bad
token and falls back by display class, rather than silently obeying
something it did not understand.

## What the disc does and does not do

Honest limits, measured rather than assumed:

- the shell **renders**; it is not yet a session — there is no input routed
  into it and no user-space process owning it (the `int 0x84` service
  surface is staged in `porting/release-1-71/`, not ported);
- `ps2-aux` binds and decodes packets, but nothing consumes the motion: no
  cursor is drawn;
- `usb-hid` is reported `absent` unless a controller exists, and there is no
  driver for it on either side of the merge;
- no network stack, no GPU command submission, no audio.

## Why it is not UEFI-bootable

This project's UEFI path is the **signed stub** (ADR-0014/0028/0029): the
firmware verifies a carrier over the kernel image before the kernel runs. A
GRUB-EFI El Torito disc would load the kernel through GRUB and skip that
verification entirely, which is the opposite of the chain's purpose. The
UEFI live medium is the ESP the stub produces (`uefi/`), not this ISO; the
UEFI boot gates (`tools/qemu_uefi_boot_test.sh`,
`tools/qemu_uefi_ms_secboot_test.sh`) are how that path is proven.
