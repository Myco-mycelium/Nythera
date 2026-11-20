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

Two discs come out of one build, because they are two different boot paths:

- `dist/nyrqis-live.iso` — GRUB/Multiboot2, **BIOS/CSM**. No disk writes;
the kernel picks one of its four device modes.
- `dist/nyrqis-live-uefi.iso` — El Torito EFI whose boot image IS the
signed stub's ESP, so firmware boots the stub, the stub walks the carrier
verification (ADR-0014/0028/0029), and only then the kernel. GRUB-EFI is
deliberately not used on this path: it would load the kernel directly and
skip that chain.

## Build it

```bash
cd nykernel-package/source/nykernel
./tools/make_live_iso.sh              # builds the kernel, then both discs
./tools/make_live_iso.sh --no-build   # package an existing target/kernel.elf
./tools/make_live_iso.sh --bios-only  # ...or --uefi-only
```

The script writes three files and prints their digests:

| file | what it is |
|---|---|
| `dist/nyrqis-live.iso` | the BIOS/CSM image (GRUB El Torito, Multiboot2) |
| `dist/nyrqis-live-uefi.iso` | the UEFI image (El Torito EFI -> signed stub ESP, ~33 MiB) |
| `dist/*.iso.sha256` | each image's digest, as recorded at build time |
| `dist/nyrqis-live.kernel.sha256` | the digest of the kernel both images carry |

## Check it

```bash
./tools/check_live_iso.sh                  # integrity + attribution
./tools/check_live_iso.sh --boot           # ...plus a headless boot of the BIOS disc
./tools/check_live_iso.sh --boot --uefi-boot  # ...plus OVMF booting the UEFI disc
```

Three separate facts, deliberately not one:

1. **integrity** — the ISO still hashes to its recorded digest;
2. **attribution** — the `kernel.elf` *inside* the ISO hashes to the
   recorded kernel digest, so a stale artifact fails instead of passing;
3. **behaviour** (`--boot`) — QEMU boots the disc headless and the kernel
   must report its device-mode verdict, bind `framebuffer` and `ps2-aux`,
   and verify the display by read-back. `--uefi-boot` does the same under
   OVMF through the signed stub, and additionally requires the stub's
   `stub alive` and `GOP` lines.

The UEFI disc has no extracted-kernel attribution: the kernel lives inside
the stub binary inside the ESP, so no file on the disc hashes to the kernel
digest. Its attribution is the build (both discs come from one run against
one `target/kernel.elf`), and the script says so instead of substituting a
weaker check.

To check the third fact the other way round -- that a disc which cannot boot
drives the gate red and mints a FAIL receipt instead of inheriting the PASS
already on record -- run the mutation:

```bash
./tools/live_iso_selfcheck.sh   # ~15 min: two real boot runs
```

It repacks `dist/nyrqis-live-uefi.iso` with an unloadable `BOOTX64.EFI` (same
El Torito layout, its own digest re-recorded, so only the boot can fail),
requires the gate to exit non-zero with `uefi: FAIL` and no booted kernel in
the receipt, then restores both discs from a backup and re-earns the PASS,
leaving `dist/` unmodified. It refuses to run without a PASS receipt covering
the discs on disk, because a red gate proves nothing if there was no claim for
a failed boot to inherit.

## Run it

Write the image to a USB stick or attach it as a CD-ROM:

```bash
# QEMU, headless, serial console on stdout
qemu-system-x86_64 -cdrom dist/nyrqis-live.iso -m 512 -serial stdio

# or to a stick (destroys the stick's contents)
sudo dd if=dist/nyrqis-live.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

### Boot managers that map the ISO (Ventoy and friends)

Ventoy does not boot the stick's bytes the way a dd'd image does: it emulates
the ISO as a CD-ROM and hands this disc's own `grub.cfg` **its** `$root`. An
absolute `/boot/kernel.elf` then misses, and every menu entry dies with
`file `/boot/kernel.elf' not found` followed by `you need to load the kernel
first` — an error that looks like a corrupt image but is only a wrong root.

The BIOS disc therefore resolves the kernel rather than trusting `$root`:

```
if ! [ -e /boot/kernel.elf ]; then
    search --no-floppy --file --set=root /boot/kernel.elf
fi
```

From a plain CD or a dd'd stick `$root` is already the disc and the line is a
no-op. Under a manager that mapped the ISO as a virtual CD it points GRUB at
the device that really holds the kernel, which is what makes the same image
boot from all three. If a particular Ventoy build still refuses the disc,
`Ctrl+R` (Ventoy's GRUB2 mode) has Ventoy load the ISO's own config from its
loopback instead of chainloading the El Torito image.

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

- the shell **renders**; it is not yet a session — no input is routed into
  it and no user-space process owns it;
- `int 0x84` exists (DPL 3) and serves `GFX_MODE` and `INPUT_STATE`; every
  other call — the release's IPC, capability, process and shared-memory
  calls — is refused with `-ENOSYS`. Pointer-taking calls wait for a
  user-range validator;
- `ps2-aux` binds, decodes packets and MOVES A POINTER: a 12x18 arrow is
  drawn into the framebuffer and restored around each move. It is not a
  window-system cursor — no hit testing, no click routing;
- `usb-hid` is reported `absent` unless a controller exists, and there is no
  driver for it on either side of the merge;
- no network stack, no GPU command submission, no audio.

## Why the BIOS disc cannot boot UEFI

This project's UEFI path is the **signed stub** (ADR-0014/0028/0029): the
firmware runs the stub, and the stub verifies a carrier over the kernel
image before the kernel runs. A GRUB-EFI El Torito image would load the
kernel through GRUB and skip that verification entirely, which is the
opposite of the chain's purpose — so the UEFI medium is the stub's ESP
(`dist/nyrqis-live-uefi.iso`), never a GRUB-EFI disc.
