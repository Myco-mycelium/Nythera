---
title: Merge release 1-71 into this kernel — boot path stays, shell ported in, four device modes, driver autoload
document_id: ADR-0031
version: 1.2.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-11-02
updated: 2026-11-02
ai_assisted: true
depends_on: [ADR-0006, ADR-0014, ADR-0028]
---

# ADR-0031 — Merge release 1-71: one kernel, one boot path, four device modes, honest drivers

## Context

Two forks of the same kernel existed in different folders:

- **this repository** (`nykernel-package/source/nykernel`): the UEFI boot
  chain (stub, Secure Boot, dbx revocation, measured boot — ADR-0014/0028/
  0029, all Accepted and gated), plus substantially larger core work:
  `experimental/elf.rs` 2783 lines vs 349, `pe.rs` 2352 vs 220,
  `scheduler.rs` 644 vs 220, `ring3_scheduler.rs` 799 vs 317, ACPI, ATA,
  RTC, kernel stacks;
- **release 1-71** (`nyrqis-release-1-71`): a BIOS/GRUB Multiboot2 kernel
  loaded unverified (its own `RULE_CHANGES.md` RC-06 says so), with the
  adaptive Material shell the release screenshots show: a user-space
  assembly shell + services, a no_std Rust `desk`, PS/2 and xHCI input,
  framebuffer, PCI, loaders and process views.

Neither is a superset. Merging them is therefore a set of decisions, not a
copy: which boot path is authoritative, what "merge the shell" means when
the shell is a user-space stack talking to a kernel ABI the other fork does
not have, and what may honestly be called a driver.

The measured ABI fact: the shell programs call `int $0x84` with the call
number in `rax`; that surface is release 1-71's
`experimental/syscall_demo.rs` (118 KiB upstream). This kernel has `0x80`
(syscall), `0x81` (yield) and its PIE/PE trap vectors, and no `0x84`.

## The decision

1. **This repository's kernel and boot chain remain the base.** The UEFI
   stub, Secure Boot and the dbx/revocation apparatus stay authoritative.
   Release 1-71's `secure_boot.rs` (BIOS image verification) is imported for
   reference only and is **not** merged: two boot-verification chains in one
   image would be two trust anchors, and the Accepted one (ADR-0014) is the
   one with gates.
2. **The shell stack is imported in-tree, not copied over.** Release 1-71's
   shell, services, `desk`, input/USB drivers, asset tooling and its design
   contract (NDL-1) live under `porting/release-1-71/` with a
   `PORT_MANIFEST.md` that names every file, its port status, and the
   measured ABI gap. Importing the sources makes the merge reviewable and
   keepable; it does not claim they run.
3. **Drivers are bound by a probe/autoload registry, and an undriven
   controller is reported, not implied.** On every boot the kernel probes
   the loader's framebuffer, the i8042 controller, ATA IDENTIFY and the PCI
   bus, then binds the drivers the tree actually ships
   (`framebuffer`, `ps2-keyboard`, `ata-pio`, `pci-bus`). A controller with
   no driver is a named `unbound` row (today: `ps2-aux`, `usb-hid`). A
   machine with no PS/2 and no framebuffer boots to console mode and says
   so.
4. **Four device modes are layout contracts, one renderer.** `desktop`,
   `tablet`, `phone`, `console` differ in status-strip height, navigation
   rail or bottom bar, column count, content-width cap and minimum touch
   target — never in palette or in code path. Selection is explicit
   (`nyrqis.mode=...`) or automatic by display class, and the boot log names
   which rule fired. An unknown token is reported rather than silently
   selecting the default.
5. **The shell renders before the `0x84` surface exists, because its
   renderer needs no syscalls.** `shell/src/ui.rs` is a pure function from a
   pixel buffer to pixels; it is compiled into the kernel from the port tree
   (`#[path]`, no copy) and drawn into the content pane each device mode's
   layout contract reserves. "The shell runs" as a *session* stays false
   until input routing and the `desk`/`0x84` port land; the remaining port
   order is input aux → the `0x84` gfx/input calls → shell process + router +
   the independent host model → `desk` → USB HID, each with a refusal-path
   test.
6. **Typography and the Material component library are not re-invented
   kernel-side.** The mode renderer draws the layout chrome those
   components will occupy; the components come with the ported shell.

## Implementation Note — the display handoff and the shell render (v1.1.0)

Two follow-ons landed after the decision, each closing a gap the v1.0.0
record named as future work.

**1. The UEFI boot path now hands over a framebuffer.** Until this
increment the stub synthesized a Multiboot2 info structure with no type-8
tag, so a UEFI boot fell back to console mode while a BIOS boot got the
graphics path — the same kernel behaving differently by boot path. The stub
now reads the firmware's current Graphics Output Protocol mode
(`boot::locate_gop`, `uefi/src/boot.rs`) and writes both the standard tag 8
and this project's scan-out-order vendor tag (`0x7d2`) into the handoff
page. Measured: `GOP 1280x800 @ 0x80000000 BGRX`, the kernel's driver binds,
the shell renders into a 1280x684 tablet pane, and the mode's read-back
verifies — on the OVMF gate, not only the BIOS one.

Refusals are explicit, because a guessed pixel format is a display that
comes up in the wrong colours and blames the driver later: no GOP, no
current mode, no mode-info block, a non-direct-colour (`BltOnly`) mode, a
bit-mask layout other than the two conventional 8-bit ones, empty geometry,
or a mode for the palette are each printed on the stub's console and
produce **no** framebuffer tag.

**2. The Material 3 home screen renders.** The v1.0.0 record said typography
and the component library would not be re-invented kernel-side and that the
mode renderer only drew chrome. That was true of the placeholder cards; it
is no longer the state of the tree. `shell/src/ui.rs` and its font table are
compiled in from `porting/release-1-71/` (one copy — the host checker and
the kernel render the same source) and drawn into the content pane that
`device_mode::content_pane` computes per mode. Two facts follow, both
measured on QEMU screendumps checked outside the guest:

- the shell picks its own window size class from the pane, so the four modes
  are genuinely different shell layouts, not one layout four times: on
  1024x768, phone's 480-px pane is `Compact` (4 columns, **no**
  quick-setting tiles — its compact rule) while desktop's 920-px pane is
  `Expanded` (6 columns, 3 tiles);
- the pixels are the *shell's*, not chrome drawn in similar colours: the
  screenshot check looks for the clock widget's `container` fill, a colour
  nothing else on the screen uses. The kernel's own read-back probe was
  moved to the same pixel after the first version probed the pane corner —
  which happens to be the same value as the mode renderer's background, so
  it would have passed on an empty pane.

This closes the merge's **rendering** gap only. The session gap is unchanged
and still the v1.0.0 decision: no input is routed into the shell, and no
user-space process owns it until the `0x84` gfx/input and process calls are
ported. `console` mode has no shell pane by construction (no pixel surface);
that is a layout fact, not a failure.

## Implementation Note — the aux driver and the live ISO (v1.2.0)

**The `ps2-aux` row is bound, not promised.** Release 1-71's `input.rs`
kernel half is `src/ps2_aux.rs`: it enables the i8042 aux port, takes IRQ12
(vector 0x2C, cascade unmasked), assembles the 3-byte packets into signed
deltas and button bits, and proves the line by asking the device for its ID
-- the device's own reply is what makes the vector accountable, with no
host input. Measured: `ps2-aux input bound`, device id 0x00, reply bytes
delivered by IRQ12. Porting it exposed two defects in my own first version,
both now fixed and documented in the module: the setup sequence's replies
were being stolen by the IRQ1 keyboard handler (the output buffer is
shared, so the polled half must run with interrupts off), and `0xFA` -- an
acknowledge -- has bit 3 set and was therefore being assembled as the first
byte of a packet, which silently ate the device ID. What is still not
claimed: no event is delivered to a shell. The release's decoders live in
user space (`ps2dec.inc`), so the driver's job today ends at "the packets
are decoded correctly and the line is real".

**A live ISO is a build artifact with a verifier.** `tools/make_live_iso.sh`
builds a bootable GRUB El Torito disc with one menu entry per device mode
(plus an automatic one), and `tools/check_live_iso.sh` answers the three
questions a committed binary otherwise leaves open: does the file still
match its recorded sha256, does the `kernel.elf` *inside* it match the
recorded kernel digest (so a stale image fails instead of passing), and
(with `--boot`) does the disc reach the device-mode verdict, bind
framebuffer and `ps2-aux`, and verify the display. Measured: 22 MiB image,
`PASS` on all three. It is deliberately **not** a UEFI-bootable disc: a
GRUB-EFI El Torito image would load the kernel through GRUB and bypass the
signed-stub carrier chain (ADR-0014/0028/0029), so the UEFI live medium
remains the ESP, not this ISO.

## Consequences

- The repository has one boot path, one trust chain, and one place where
  the shell's port status is written down. A reviewer can see exactly what
  is running and what is staged, and the manifest's gap section is
  falsifiable by reading the two IDT vector tables.
- `tools/device_mode_test.sh` makes the display and the modes a measured
  fact on every push: four headless QEMU boots, one per mode, QEMU
  screendumps re-checked by a checker outside the guest, distinct
  screenshots, and the automatic-selection and invalid-token paths. The
  kernel's own read-back is not sufficient on its own — a wrong physical
  address still reads back the pixels just written.
- The four modes are new user-facing behaviour with a real cost: a
  framebuffer request in the Multiboot2 header changes the boot display
  mode for every GRUB boot, and the driver binds only for direct-RGB 32bpp
  (anything else is a named refusal).
- Not decided here: USB HID on real hardware, the `desk` port's API surgery
  against the reworked scheduler/IPC paths, and whether the release's
  BIOS-side secure boot is ever brought forward as a second, subordinate
  check. Each is future work with its own record if it becomes a decision.
