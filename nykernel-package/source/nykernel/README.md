# Nyrqis Kernel

A real, boot-verified x86_64 kernel for the Nyrqis platform (hybrid
microkernel per ADR-0006), built in phases against the repo's own
governance rules (NPC-001 §3.1/§6 -- see `engineering/RFC-0001-nykernel-bringup-plan.md`
at the repo root). This file is the entry point; it tells you what
this actually is and how to try it. For the full evidence -- every
boot transcript, every bug found and fixed, every negative control --
see `IMPLEMENTATION_STATUS.md` (this directory) and `PHASES.md` (repo
root).

## What this is, in one paragraph

A Multiboot2-bootable x86_64 kernel, written in Rust (`no_std`) plus a
small hand-written assembly boot stub, that boots to a running,
interrupt-driven, memory-managed state (Phases 1-2, grounded in the
already-`Accepted` NPS-001/NPS-017), then demonstrates -- as explicitly
labeled, pre-normative proofs of concept, not NPS-002/NPS-003
conformance -- cooperative multitasking, timer-driven (preemptive)
ring-3 task switching, inter-task IPC, real ring-3
(user-mode) execution with genuine ELF loading (both fixed-address and
position-independent), per-process address-space isolation integrated
into the scheduler, and real memory reclamation. On top of those
prerequisites, it also now loads and executes a real, genuine Windows
PE32+ executable's own code in ring 3 -- the first actual Phase 5
capability, not just another prerequisite -- and parses and validates
a real, checksum-correct Android DEX file's container format, the
first actual Phase 6 capability. Every claim below is backed by an
actual QEMU boot transcript, not a design document.

## Try it

On a fresh Debian/Ubuntu host, `../../setup.sh` (run from this
directory) installs every dependency -- the `apt` packages, plus
`rustup` and the `rust-src` component -- and then runs both commands
below. It is idempotent and takes `SUDO_PASSWORD` for non-interactive
`sudo`.

```bash
./build.sh                    # build the kernel + all test userprograms
./tools/qemu_boot_test.sh     # automated boot test, real pass/fail exit code

# or, to watch it boot interactively:
./build.sh
mkdir -p target/isofiles/boot/grub && cp target/kernel.elf target/isofiles/boot/
cat > target/isofiles/boot/grub/grub.cfg <<'EOF'
set timeout=0
menuentry "Nyrqis Kernel" { multiboot2 /boot/kernel.elf; boot }
EOF
grub-mkrescue -o target/nyrqis.iso target/isofiles
qemu-system-x86_64 -cdrom target/nyrqis.iso -serial stdio -display none
```

Requires: `rustc`/`cargo` (current stable -- see "Toolchain notes" in
`IMPLEMENTATION_STATUS.md`), the `rust-src` component (`rustup component
add rust-src`), GNU `as`/`ld`, a `rust-lld` (it ships inside the rustup
toolchain; `build.sh` locates it automatically), `grub-mkrescue`/`xorriso`,
and `qemu-system-x86_64`. On Ubuntu the last three are `apt` packages;
`build.sh` and `tools/qemu_boot_test.sh` are self-contained otherwise.

Without root, `./tools/install_boot_tools.sh` installs QEMU, GRUB, xorriso
and mtools into a user-space root (proot + an Ubuntu base rootfs) and puts
shims on `PATH`, so the boot gates run unchanged.

`./tools/gate_env.sh` checks what the gates assume -- a path short enough for
a 108-byte unix socket (proot's monitor-socket trap), the QEMU/GRUB pair, the
`/tmp/nybin` shims, clippy, and whether `/usr/bin/time` exists (on this image
it does not) -- and names the broken assumption instead of leaving a 127 or a
hang. All three boot gates run it first (`--quiet`), so a missing requirement
fails at the top with a sentence rather than in the middle with a 127.

`build.sh` runs two style gates on every build, so all four boot gates inherit
them. Both skip cleanly when clippy is absent (install it with `rustup
component add clippy`; `rust-toolchain.toml` lists it), and
`SHADOW_CHECK_REQUIRED=1` turns that skip into a failure -- which is what CI
sets, so a runner without clippy fails there rather than passing with the
check never having run.

* `./tools/shadow_check.sh` turns on clippy's `shadow_unrelated` /
  `shadow_same` lints (off by default, so clippy's silence never meant
  anything) and fails the build on any shadowing beyond
  `tools/shadow_baseline.txt` -- the bug class that once let a failing
  block-device claim print another leg's record and still say `matches`. The
  baseline is now EMPTY: all ten recorded sites were renamed rather than
  excused. `--update` refuses to baseline more than `SHADOW_GROW_MAX` (2) new
  sites unless `--allow-grow` is passed, so "just re-baseline it" cannot
  erase the allowance in one command.
* `./tools/lint_ratchet.py` applies the same ratchet to the lints whose
  failure mode is an abort or a wrong table row: `unwrap_used`,
  `expect_used`, `indexing_slicing`, scoped to `src/` minus `src/porting/`
  and `src/experimental/`, baselined in `tools/lint_baseline.txt`.
  `as_conversions` and `arithmetic_side_effects` are deliberately NOT
  ratcheted yet (842 and 404 sites in scope: they need a conversion pass
  first, not a baseline nobody can review). `SKIP_LINT_RATCHET=1` opts out.

The verdict function writes the words `matches`/`MISMATCH` in exactly one
place (`verdict_word!` in `src/service84.rs`) and folds every printed leg
into the running verdict through `claim!`, so a leg cannot be printed without
being counted. That is not cosmetic: before this, eight printed legs
(`cap_ok`, `ipc_ok`, `shm_ok`, `reply_ok`, `input_poll_ok`, `two_ok`,
`map_ok`, `peer_ok`) were missing from the hand-written `ok` chain and
`stored_ok` was counted twice -- the block-info bug's shape, one layer up.

`./tools/gate_selfcheck.sh` is the meta-gate: it takes a real transcript
(`target/svc84-proof-last.log`, saved by every passing proof run), deletes one
piece of evidence at a time -- the CPL 3 claim, a leg's `matches`, the
kernel's `-- OK` -- and requires the proof gate to reject each mutation for
the right reason, while the unmutated transcript still passes. The proof gate
also COUNTS the distinct legs in the verdict (26) so a leg that stops running
cannot leave a smaller, quieter green.

`./tools/run_gate.sh <gate>` wraps any gate: it keeps the transcript in
`target/receipts/`, writes a JSON receipt (verdict, OK/PASS/FAIL line counts,
leg count, pinned hashes) and compares it with a reference, so a verdict that
shrank is reported as drift instead of scrolling past. The reference is the
previous run here if there is one, otherwise the committed known-good receipt
in `tools/receipts_known_good/` -- which is why a fresh clone can diff its
first run. With neither it prints `drift comparison SKIPPED` and the command
that fixes that, rather than a reassuring nothing. `--record` promotes a
passing run's receipt to the committed reference (refreshed deliberately, in a
reviewable diff; growth in the counts is never drift, only shrinkage is). CI
runs the BIOS and device gates through it, requires the receipt to say PASS
when the gate does, and uploads `target/receipts/` as an artifact.
The proof gate can also replay a saved transcript with no QEMU
(`./tools/svc84_proof_test.sh --replay <log>`), which is what makes the
meta-gate possible and what makes a verdict re-checkable in a second.

### What these gates cannot see

A green board is not a proof of everything, and the gaps are worth naming:

| Gate | Blind spot | Why it is not covered yet |
|---|---|---|
| shadow check | field/type-name reuse, and `src/porting/` by scope | those are not locals; the lint is about locals |
| abort ratchet | `as` truncation and arithmetic wrap | 1246 sites in scope: needs a conversion pass before a baseline can mean anything |
| proof gate | a per-row REFUSAL matrix beyond the thirteen cells probed: 43 rows are served, and a row that validates before acting is only proven to refuse the arguments `tools/svc84_refusals.md` names (forged handle, zero or illegal length, misaligned non-user pointer, absent state). The matrix (`REFUSAL_MATRIX` in `src/service84.rs`, probes at `BUF+12288`) pins each probed cell's errno and the gate pins the two packed words; rows that mutate before they validate need a probe in their own process | the remaining rows are a `RefusalRow` plus ~10 instructions each (`tools/svc84_refusals.md` has the steps); the known blockers are rows whose refusal can only be reached after they already allocate or queue (`0x4f` is a cell, not a blocker: it settles its length before it maps) |
| proof gate | timing and interrupt interaction during the proof | the gate asserts values, not schedules |
| boot gates | anything the kernel prints but does not assert | the gate's patterns are the contract |
| device gate | layout correctness beyond the measured landmarks | screendumps are checked at named coordinates |

## What's proven, at a glance

| Capability | Where | Status |
|---|---|---|
| Multiboot2 boot, long mode, GDT/IDT, exception handling | `boot/boot.s`, `src/gdt.rs`, `src/idt.rs` | ✅ NPS-001-grounded |
| Physical frame allocation + dynamic paging + kernel heap | `src/memory/` | ✅ NPS-001-grounded |
| PIC remap + real hardware timer interrupts | `src/pic.rs` | ✅ NPS-001-grounded |
| RTC periodic interrupt (IRQ8, slave-PIC cascade) | `src/rtc.rs` | ✅ NPS-001-grounded |
| PS/2 keyboard interrupt (IRQ1, input-free injection test) | `src/keyboard.rs` | ✅ NPS-001-grounded |
| ATA/ATAPI IDE interrupts (IRQ14/15, conditional on a device) | `src/ata.rs` | ✅ NPS-001-grounded |
| NX (no-execute) enforcement, W^X | `src/memory/paging.rs`, `src/experimental/elf.rs` | ✅ verified with a positive control |
| Cooperative kernel-mode task scheduling | `src/experimental/scheduler.rs` | 🔶 pre-normative (NPS-002 Draft) |
| Inter-task IPC (mailboxes) | `src/experimental/ipc.rs` | 🔶 pre-normative (NPS-003 Draft) |
| Ring-3 (user-mode) execution | `src/experimental/usermode.rs` | 🔶 pre-normative |
| Minimal ELF64 loader (`ET_EXEC`) | `src/experimental/elf.rs` | 🔶 pre-normative |
| Position-independent (`ET_DYN`) loading + relocations | `src/experimental/elf.rs`, `pie_demo.rs` | 🔶 pre-normative |
| Per-process address-space isolation | `src/experimental/address_space.rs`, `isolation_demo.rs` | 🔶 pre-normative |
| Address-space switching integrated into the scheduler | `src/experimental/isolated_tasks.rs` | 🔶 pre-normative |
| Real ring-3 task scheduling, 3 tasks (full-GPR trap handler) | `src/experimental/ring3_scheduler.rs` | 🔶 pre-normative |
| Timer-driven (preemptive) ring-3 switching of non-yielding tasks | `src/experimental/ring3_scheduler.rs`, `userprog/prog_spin.s` | 🔶 pre-normative (NPS-002 Draft) |
| Both hash mechanisms: DT_HASH walk + glibc-style DT_GNU_HASH walk (answer equality pinned) | `src/experimental/elf.rs`, `elf_dyn_deps_demo.rs` | 🔶 pre-normative |
| Depth-3 catalog-driven dependency chains + weak `VER_FLG_WEAK` artifact | `src/experimental/elf.rs`, `elf_dyn_deps_demo.rs`, `tools/patch_weak_verneed.py` | 🔶 pre-normative |
| Address-space teardown + frame reclamation | `src/experimental/address_space.rs`, `teardown_demo.rs` | 🔶 pre-normative |
| TLB invalidation (`invlpg`) on unmap | `src/memory/paging.rs`, `tlb_demo.rs` | ✅ NPS-001-grounded |
| Guard pages for stack-overflow detection (probe) | `src/memory/guard_demo.rs` | ✅ NPS-001-grounded |
| Guarded kernel stacks under every live task | `src/memory/kernel_stack.rs`, `experimental/address_space.rs`, `experimental/scheduler.rs` | ✅ NPS-001-grounded |
| ACPI RSDP validation + reclaimable-memory release | `src/acpi.rs`, `src/memory/frame_allocator.rs` | ✅ NPS-001-grounded |
| Minimal PE32+ (Windows) loader | `src/experimental/pe.rs`, `pe_demo.rs` | 🔷 first real Phase 5 capability |
| PE32+ import resolution: GetProcAddress-complete (named/ordinal/forwarded exports) + bound imports (stamp-verified honor / visible stale discard, forwarder refs incl. two-ref chains) + delay-load imports (eager resolve through the same registry, typed refusals of old-BIND descriptors and of structural corruption: unknown grAttrs, broken INT==IAT, pre-VC7 VA form, and a real VA-form descriptor in a relocated image; the descriptor-array 16 / thunk-array 64 caps proven EXCLUSIVE from both sides -- 17 descriptors and 65 thunks refused, exactly 16 with exactly 64 accepted) | `src/experimental/pe_import_demo.rs` | 🔷 Phase 5 increment |
| PE32+ exception-table (`.pdata`) walk: startup `IMAGE_DIRECTORY_ENTRY_EXCEPTION` composed from the client's own section-table facts by a checked patcher, runtime-function entries bounds-checked against the section table, UNWIND_INFO version nibble walked; typed `MalformedExceptionTable` refusals every boot (genuinely overlapping second entry, undefined ABI version 3) | `src/experimental/pe_exception_demo.rs`, `tools/patch_pe_pdata.py` | 🔷 Phase 5 increment |
| PE32+ resource-directory walk: startup `IMAGE_DIRECTORY_ENTRY_RESOURCE` composed the same way, two-level resource tree walked to a ring-3 leaf string; typed `MalformedResourceDirectory` refusals every boot (leaf size past the image footprint, empty length-0 name string, array slot past the named/ID cap) | `src/experimental/pe_resource_demo.rs`, `tools/patch_pe_res.py` | 🔷 Phase 5 increment |
| Minimal DEX (Android) parser + checksum/SHA-1 verify | `src/experimental/dex.rs`, `crypto.rs`, `dex_demo.rs` | 🔷 first real Phase 6 capability |
| Platform identities: native Nyrqis (the mycelium mark), Android, Windows, macOS and console postures -- palette + chrome contract each, auto-detected per machine from the ACPI FADT OEM ID, PCI vendor IDs and the panel's own width, selected explicitly with `nyrqis.persona=`, and switchable at runtime with keys 1..5 (every switch repaints and re-verifies) | `src/platform_mode.rs`, `src/acpi.rs`, `src/devices/pci.rs`, `src/lib.rs` | ✅ implemented, self-tested on boot |
| Console mode's prompt and session lines rendered with the release's real 5x7 font instead of rule-block stand-ins | `src/device_mode.rs` | ✅ placeholder removed |

🔶 **"Pre-normative" is a real, repo-enforced distinction, not hedging.**
NPS-002 (Process/Thread Model) and NPS-003 (IPC/Capability Passing) are
still `Draft` per `NPC-004 Specification Index`; per `NPC-001 §5`, a
Draft spec isn't binding. Every 🔶 item above lives in
`src/experimental/` specifically so that's visible in the source tree,
with an explicit banner in `src/experimental/mod.rs`. None of it should
be read as "the kernel's real process model" -- it's a technology
proof for the mechanical primitives NPS-002/003 will eventually need
underneath their actual policy.

🔷 **Marks the one item that's different in kind, not just degree.**
`experimental::pe` isn't a mechanical prerequisite for Phase 5 -- it's
an actual first piece of Phase 5 itself (loading Windows' own
executable format). It's still not Windows *application* compatibility
(see "What's honestly still missing" below), but it's a genuinely
different category of progress than everything else in this table.

## Platform identities

`crate::device_mode` picks the **form factor** (desktop, tablet, phone,
console) from the panel. `crate::platform_mode` picks the **identity**
the session presents on it, and the two are independent:

| identity | chrome | accent |
|---|---|---|
| `nyrqis` | the kernel's own look: spore-cluster mark, wordmark, accent rule under the status strip | bio-luminescent mint `#2eeea6` |
| `android` | status strip with an accent chip, bottom gesture pill | Material You blue `#a8c7ff` |
| `windows` | bottom taskbar with the four-square start mark, window title bar with minimise / maximise / close | Windows blue `#4cc2ff` |
| `macos` | top menu bar (mark, menus, clock), bottom dock, traffic lights on the content window | system blue `#0a84ff` |
| `console` | no desktop chrome: terminal frame with the identity mark and wordmark | terminal green `#3ddc84` |

Selection is a per-machine decision printed with the signal behind it:

* explicit — `nyrqis.persona=nyrqis|android|windows|macos|console` on the
  loader command line; an unknown token is reported, never silently
  replaced by the default;
* automatic — the ACPI FADT's OEM ID first (`APPLE`, `MSFT`), then the
  PCI vendor IDs on the bus (`0x106b` Apple, `0x1414` Microsoft), then a
  phone-sized panel (`< 800 px` wide) as an Android posture, otherwise
  the native identity;
* runtime — keys `1..5` during the boot session's one-second hotkey
  window call the same `platform_mode::switch` a session owner would:
  the device chrome is repainted first (no pixel of the previous
  posture can survive), the identity is applied, its landmarks are read
  back, and the surface hash is published.

The `qemu_test` boot gate (or `nyrqis.persona_selftest=1`) additionally
runs the cycle proof: all five identities are rendered, each one's
read-back landmark is checked, the five surface hashes are asserted
distinct, and the selected identity is restored. The live ISO's GRUB
menu carries one entry per identity beside the per-device-mode entries.

A ring-3 session can drive the identity itself through the `0x84` gate:
`PLATFORM_GET` (0x5d) answers the live identity, `PLATFORM_SET` (0x5e)
switches and repaints it (verified through the skin's own landmark,
refused `-EINVAL`/`-ENODEV`/`-EIO` by name), and `PLATFORM_INFO` (0x5f)
writes one record -- identity, form factor, display width and height,
status-bar height and accent -- that the boot proof compares word-for-word
against the kernel's live values. `PLATFORM_MODE_SET` (0x60) is the other
direction: it re-renders the device chrome at a requested form factor,
re-applies the current identity, publishes the new layout only after both
read-backs verify, and is therefore refused `-EIO` (nothing published)
when either render fails.

Who may change the display is decided by a small supervisor in
`src/display.rs`, and there are two forms of it. `DISPLAY_SERVE` (0x64)
makes the calling session the display SERVER and publishes a POLICY mask
(`1` identity, `2` form factor, `3` both); `DISPLAY_ATTACH` (0x65) then
attaches another session as a CLIENT, granted `requested & policy` and
returned as `(slot << 32) | grant`; `DISPLAY_DETACH` (0x66) frees the
slot, `DISPLAY_CLIENT` (0x67) queries any session's grant, and
`DISPLAY_STATUS` (0x68) answers `server | client count | caller grant` in
one word. The server always may mutate; a client may call exactly the rows
its own bits name, and nothing else gets through -- `-EACCES`. Without a
server, `DISPLAY_ACQUIRE` (0x61) is the older, blunter form: one session
holds the display outright (`DISPLAY_OWNER` 0x63 answers it,
`DISPLAY_RELEASE` 0x62 gives it back), and the two forms refuse each other
with `-EBUSY`.

A server outlives its clients by construction: a client's withdrawal frees
its slot and nothing else, so the policy and the remaining clients keep
supervising the display. A server's own withdrawal dissolves it and
detaches every client, because an orphaned policy would refuse every later
session for the rest of the boot. The identity and form factor are kernel
state, so they survive both -- the authority dies, the picture stays.

Each client can then own a surface, and a surface is a MAPPED REGION.
`DISPLAY_SURFACE` (0x69) requests one page for the calling client, maps it
into a window the kernel chooses, mints the caller a capability over it,
and hands back `{base, len, rights, handle}` -- the client DRAWS INTO `base`
with ordinary stores. `DISPLAY_COMPOSE` (0x6a) is the server's alone: it
reads each client's region through the **window the region machine mirrored
into the server's own address space** (same frames, same VA, installed by
`allocate_region_mapping` when the region was requested), blits the tiles
onto the display in slot order, reads its own work back, and answers how
many tiles it composed. That mirror is the cross-address-space window -- no
CR3 switch, no copy -- and it is the same mechanism that made the earlier
capability handover work. A client can only address its own slot: the slot
comes from the kernel's client record, not from arithmetic, and the surface
goes away with the slot.

Two facts had to stop being proof-only for that to work on a plain boot.
`src/experimental/space_registry.rs` is a small table from SESSION id to the
PML4 of the space that session runs in: the 0x84 proof registers its two
processes when it builds their spaces, a display server registers the space
it is running in when it starts serving, and a session's row leaves with its
withdrawal. `src/experimental/region_window.rs` installs the general region
windows over the boot's own frame allocator: a requested region is mapped at
a window of its own, and mirrored into the DISPLAY SERVER's registered space
when that space is known and different -- which is what lets composition read
a client's surface in a boot that never ran the 0x84 proof. When the server
shares the caller's space, no mirror is needed and the same address is the
window. Windows are SLOTS rather than an ever-advancing address: a window's
base comes from its slot, a release clears the slot so its base is handed out
again, and each slot remembers the space its mirror was placed in so the
release removes the mirror from the space that actually has it. That is what
keeps a server that opens and closes surfaces for hours down to the same few
windows.

Surface tiles come from a SECOND table of the same shape, deliberately
separate from the capability machine's region windows: a surface is opened,
drawn into, composed and given back for as long as a server runs, so it must
not spend the same budget as a transferable region -- and on the proof boot
it must not take one of the monotonic window slots the region verdict pins.
`DISPLAY_SURFACE` (0x69) maps the tile from that table and mints the
capability over the mapping the kernel already made, so nothing in the
capability machine owns those frames; `DISPLAY_SURFACE_RELEASE` (0x6c) gives
the record back first, then the capability, then the window itself -- frames,
mirror and slot -- and answers the table's packed live and issued counts, so
a caller can watch the issue count climb while the live count returns to
where it started. A session that withdraws gives its surface window back with
it. `DISPLAY_SPACES` (0x6b) reports the registry as three facts -- how
many sessions are registered, whether the caller is one, whether the server
is -- and never the PML4 addresses themselves.

## How it's organized

```
boot/boot.s          Multiboot2 header, 32->64-bit transition, identity paging
linker.ld             Load-address layout, kernel image symbols
src/
  lib.rs              kernel_main -- the boot sequence, in order
  gdt.rs, idt.rs       GDT/TSS, IDT, CPU exception handlers
  acpi.rs              RSDP validation, RSDT/XSDT enumeration (reclaim support)
  ata.rs               ATA/ATAPI IDE channel interrupts (IRQ14/15)
  pic.rs               8259 PIC remap, timer interrupt
  rtc.rs               CMOS/RTC periodic interrupt (IRQ8)
  keyboard.rs          PS/2 keyboard interrupt (IRQ1) + injection self-test
  multiboot.rs         Multiboot2 info-structure parsing
  memory/
    frame_allocator.rs Physical frames -- allocate + free-list deallocate
    paging.rs           Dynamic page mapping, NX enable
    heap.rs             Kernel heap (#[global_allocator])
    tlb_demo.rs         TLB-invalidation-on-unmap self-test
    guard_demo.rs       Guard-page stack-overflow detection + proof
    kernel_stack.rs     Shared guarded kernel stacks (all live task stacks)
  experimental/         Everything NOT NPS-001/017-grounded -- see its
                         own mod.rs banner before assuming any of it is
                         spec-conformant
tools/qemu_boot_test.sh Automated boot test (real pass/fail exit code)
userprog/                Real, separately-built test executables the
                          kernel loads and runs (not synthetic bytes)
```

## Every claim here is boot-verified, not asserted

Every capability in the table above has an actual transcript in
`IMPLEMENTATION_STATUS.md` from a real `qemu_boot_test.sh` run, most
with an accompanying **negative control** -- a temporary, deliberately
broken build that confirms the check would actually catch the failure
it claims to catch, reverted immediately after. Several negative
controls this session produced results *different* from what was
predicted (failing louder or earlier than expected) -- documented as
they actually happened, not adjusted to match the prediction. Real
bugs found along the way are documented with root cause, not just
"fixed" -- see IMPLEMENTATION_STATUS.md's "Bugs found and fixed"
sections throughout.

## What's honestly still missing

Read `IMPLEMENTATION_STATUS.md`'s "What does NOT exist yet" section in
full before assuming this kernel does something it doesn't. Headlines:
the `ld.so`-grade remainder of dynamic linking (the in-object,
registry-scoped cross-object, AND full-contract halves landed
2026-10-13/14: `GLOB_DAT`/`JUMP_SLOT`/`64` relocations,
DT_HASH/DT_SYMTAB/DT_STRTAB, by-name SysV lookup, a documented
self-first/load-order-resolution registry, enforced `DT_NEEDED`/`DT_SONAME`
dependency contracts, `DT_VERSYM`/`DT_VERDEF`/`DT_VERNEED` versioned
symbol resolution, and trap-based LAZY PLT binding through a genuine
shared service; the seventh round (2026-10-14) additionally closed:
recursive `DT_NEEDED` discovery through a named in-memory catalog
(the storage client, when Phase 8 lands, replaces the catalog's file
entries and the loader's shape stands), `SymbolRegistry::interpose`
interposition overlays beyond load order, weak-version symbol
semantics (`STB_WEAK`/`VER_FLG_WEAK`: tolerated-unresolved resolves
the NULL slot, never a guess), and a readelf CI gate that refuses the
BUILD on fact drift in the embedded .so files; the eighth round
(2026-10-15) additionally closed: DT_GNU_HASH as a real second lookup
mechanism (glibc-style bloom + bucket/chain walk, GNU hash function,
GNU-preferred resolution with DT_HASH kept as the authority for the
symbol count and the GNU table's undefined-symbol exclusion, and the
two walkers' answer equality pinned every boot), per-stage guarded
RSP0 stack ownership (`run_on_fresh_stack`; the shared default is back
to 5 pages,the
deepest stage declares its own), a DEPTH-3 catalog
chain (dep → B → C → D → A, all link-recorded edges,
auto-load count == 3, per-provider marker immediates pinned at each
loaded base, and the depth cap checked by a real negative control),
and the weak VERNEED
flag on a real readelf-verified artifact (built by a checked, gate-
pinned patch step that discloses rather than simulates the toolchain,
and whose idempotence is verified with a real second run), a lazy-
binder typed-refusal negative control (unregistered trampoline page =
typed `UnregisteredTrampoline`, handler panic demonstrated; the
EMPTY-registry branch pinned as typed `ServiceEmpty` at the site of
the chain's first registration), and a
guarded-stack exact-slot reuse mechanism (allocate/release/re-hand-out
byte-identically, guard intact, exact page-count matching proven both
directions, whole-slot absorption for smaller requests (best-fit,
never split — the guard invariant is the reason), and the FIRST LIVE
hand-back proven through the real
teardown site — `AddressSpace::destroy` routes its reclamation through
`kernel_stack::release`, so the ring-3 scheduler's destroyed task F's
RSP0 slot is re-handed out as another live task's stack, boot-proved) —
plus a CI-gate rule pinning the kernel's negative-control scratch
windows against artifact PT_LOAD ranges — and the ninth round
(2026-10-17) closed the GNU OWN-`.plt` lazy protocol itself: the
dependent's REAL linker `.plt`/`.got.plt` machinery (GOT[1] = a
per-object link-map id cookie, GOT[2] = the user-executable resolver
thunk, JUMP_SLOT slots at GNU lazy bias state), the real
cookie/index/caller-ret spill identification, resolution through the
same registry + version contract, and the guided return that drops
only the two spilled words and enters the resolved callee directly,
with one registry serving the 0x88 trap-stub path AND the 0x8B GNU
protocol path, where the same dependent artifact loaded TWICE at
two bases carries two distinct link-map cookies, each bound
EXACTLY once through the one gate, and a THIRD genuinely different
artifact (linked against both providers) carries a MULTI-ENTRY
.plt relocation table under ONE cookie -- two first-use binds,
each resolved by its own index -- and the protocol is OPT-IN PER
LOAD with its own absence pinned: requested on a PLT-LESS GLOB_DAT-
only dependent it is a typed `MalformedDynamicTables` refusal, and
the same artifact loads eagerly with ZERO GNU side effects -- but GNU-`ld.so` resolver internals (a
ring-3-executable resolver body with its own private hash tables,
and symbol merging across unregistered scopes) stay open;
unresolvable requirements are still
refused with typed errors (except the weak NULL-slot rule), never
mis-resolved), and -- the
largest
gap by far --
everything an actual Windows or Android compatibility layer needs on
top of these kernel-level prerequisites: import handling for real
Windows binaries, a DEX/ART-equivalent runtime that
actually executes bytecode, the actual API surfaces. See `PHASES.md`
(repo root) Phase 5/6 for that scope, stated precisely.

## Governance

This kernel is built against `NPS-001` and `NPS-017` (both `Accepted`)
and the language rule in `ADR-0020` (Rust/C++/C for platform-critical
paths -- this kernel is 100% Rust + hand-written assembly, no Python
anywhere). See `engineering/RFC-0001-nykernel-bringup-plan.md` (repo
root) for the full alignment check, including what remains an honestly
open action item (NPS-007's exact text on the Windows kernel-mode
exclusion boundary, not yet retrievable in this environment).
