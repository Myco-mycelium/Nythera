# Release 1-71 import — port manifest

This directory is the **in-tree copy of the shell and device stack from
`nyrqis-release-1-71`**, imported so the merge is one repository instead of
two folders. It is source-of-record for what the release built. Most of it is
staged; two files are **compiled by our kernel right now** (`shell/src/ui.rs`
and `shell/src/font_data.rs`, via `#[path]` in `src/lib.rs` — no copy, so the
host checker and the kernel render the same source). Every file below is
listed with its port status, and the measured ABI gap that blocks the rest is
in the last section.

Provenance, so the copy can be re-derived rather than trusted:

- upstream: `nyrqis-release-1-71/nyrqis-release-1-71/nykernel-package-source.zip`
- zip SHA-256: `466dd8dbf5c4d71121e377df2aecf3685ed2f24dfcdecdf94d1004bb97979233`
  (the release's own `SHA256SUMS`)
- imported 2026-11-02; 46 files, 796 KiB; binaries (`*.iso`, `target/`) are
  deliberately not imported.

## Status

| Imported path | Port status | Where it lives now / what is missing |
|---|---|---|
| `src/experimental/framebuffer.rs` | **ported** | `src/devices/framebuffer.rs` (owned by the autoloader, not a demo), plus the Multiboot2 tag-8 decoder in `src/multiboot.rs` and the header request in `boot/boot.s` |
| `src/experimental/pci.rs` | **ported** | `src/devices/pci.rs`, widened from the USB-controller mask to the inventory the autoloader reports |
| `src/experimental/input.rs` | **ported (kernel half)** | `src/ps2_aux.rs`: i8042 aux enable, IRQ12 handler (vector 0x2C, cascade unmasked), 3-byte packet assembler with signed deltas and button bits, device-ID/ACK protocol-word handling, and a boot self-test. Measured on QEMU: `ps2-aux input bound`, device id 0x00, reply bytes delivered by IRQ12. Two defects found while porting, both recorded in the module: the polled setup sequence lost its replies to the IRQ1 keyboard handler until it ran with interrupts off, and `0xFA` (bit 3 set) was being assembled as a packet start. The release's *decoders* stay in user space (`ps2dec.inc`), so nothing is delivered to a shell yet |
| `src/experimental/usb.rs`, `xhci.rs` | staged | USB HID pointer; the autoloader reports `usb-hid unbound` on machines that have a controller, rather than pretending |
| `src/experimental/syscall_demo.rs` | staged — **blocking** | the `int 0x84` service surface (capability/IPC/process/gfx/input). See the gap below |
| `src/experimental/capability.rs`, `shm.rs` | staged | used by the `0x84` surface |
| `src/secure_boot.rs` | staged — **not** part of this merge | it is the release's BIOS/GRUB boot verification; this repository's UEFI chain (ADR-0014/0028/0029) stays authoritative |
| `userprog/prog_shell.s`, `prog_livesh.s`, `prog_kbdrt.s` | staged | the shell UI, its live session and the keyboard router |
| `userprog/prog_gfxsvc.s`, `prog_inputsvc.s`, `prog_typist.s`, `prog_surf.s`, `prog_ntf.s`, `prog_rtcd.s`, `ps2dec.inc` | staged | the user-space services the shell talks to |
| `userprog/desk/` | staged | the no_std Rust desktop (`desk`), with its assets and independent renderer |
| `shell/src/ui.rs`, `shell/src/font_data.rs` | **ported and running** | compiled into the kernel from this path (`src/lib.rs`'s `#[path]` modules), drawn into each device mode's content pane by `src/device_mode.rs`. `ui.rs` is a pure renderer (a pixel buffer in, no syscalls), which is why it needed no part of the `0x84` surface. `font_data` sits at the crate root because `ui.rs` names `crate::font_data` |
| `shell/host/hostrender.rs` | staged | the independent host renderer/checker; it needs a std host build, not the kernel |
| `tools/*` | staged | asset generators (Roboto/icon rasterizers) and the shell checkers; they need Python `fontTools` + Pillow, and Chromium for the re-render checks |
| `docs/UI_DESIGN_LANGUAGE.md` | imported | NDL-1, the design contract the four device modes implement |
| `docs/RULE_CHANGES.md` | imported | the release's rule-deviation register (its RC-IDs are referenced by the imported code) |

## What runs today (our kernel)

`src/device_mode.rs` and `src/devices/` implement, on this repository's
kernel and boot path:

- the hardware probe (framebuffer tag, i8042, ATA IDENTIFY, PCI scan) and the
  driver autoload transcript, including drivers that are **not** bound and why;
- four device modes (`desktop`, `tablet`, `phone`, `console`) with their real
  layout contracts, selected by `nyrqis.mode=` or by display class, drawn to
  the framebuffer and verified by read-back *and* by an independent QEMU
  screendump check (`tools/device_mode_test.sh`);
- **the Material 3 home screen itself** (`shell::render`, i.e. `ui.rs` above)
  in every mode but console, into the content pane that mode's contract
  reserves. The shell's own window-size-class logic then picks a different
  real layout per pane width: measured on a 1024x768 display — desktop pane
  920x588 -> `Expanded`, 6 columns, 3 quick-setting tiles; tablet pane
  1024x652 -> `Expanded`; phone pane 480x632 -> `Compact`, 4 columns, **0**
  tiles (its compact rule), and 1024x768 UEFI -> tablet pane 1280x684.

What is **not** running: the shell as a *session*. There is no input routed
into it and no user-space process owning it (`userprog/desk` needs the full
`0x84` IPC/capability/shm surface, and the shell's PS/2 router is assembled
user-space code that talks to it). What runs today is the shell's rendering
path end to end — the same source the release's `nyshell` process calls,
drawing into the kernel's real framebuffer mapping.

## The measured ABI gap (why the shell programs do not run yet)

The shell stack talks to the kernel over **`int $0x84`** with the call number
in `rax` (0–3, 10, 20–22 in `prog_gfxsvc.s` / `prog_inputsvc.s` /
`prog_shell.s`). That surface is `src/experimental/syscall_demo.rs`
(118 KiB upstream). This kernel currently has:

| vector | this repository | release 1-71 |
|---|---|---|
| `0x80` | ring-3 syscall (existing demos) | ring-3 syscall |
| `0x81` | yield / task switch | yield / task switch |
| `0x84` | **absent** | capability/IPC/process/gfx/input service calls |

Porting order that keeps every step verifiable:

0. ~~the shell's renderer~~ — **done** (see above). It was possible before
   step 2 because `ui.rs` draws into a caller's buffer and makes no calls;
   the ordering that remains is about *input and a session*, not pixels;
1. port `input.rs` (PS/2 aux) and register it, replacing the `ps2-aux
   unbound` row with a bound one;
2. port the `0x84` surface's **gfx and input** calls only, plus the
   `prog_gfxsvc` / `prog_inputsvc` services, and drive them from a kernel-side
   test before any shell pixel is drawn;
3. port `prog_shell` + `prog_kbdrt` and the `shell/` host model, then assert
   the shell's own pixel-for-pixel check (the release's `check_shell.py`) on
   this tree;
4. port `userprog/desk` last: it is the largest surface and it consumes the
   scheduler/IPC paths our tree has reworked (our `experimental/scheduler.rs`
   and `ipc.rs` are larger than the release's, so that port is API surgery,
   not a copy);
5. `usb.rs`/`xhci.rs` when a USB HID pointer is wanted on real hardware.

Each step lands with its own refusal-path test; no step is "copy the file and
see".
