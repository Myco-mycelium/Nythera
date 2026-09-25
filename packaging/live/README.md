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

### Building without root (user namespace)

No sudo on the build machine? `build-live-iso-rootless.sh` produces the
**same ISO through the unmodified builder** using a user namespace and a
tiny LD_PRELOAD shim (`rootless-syscall-shim.c`) that covers the only
two operations a self-mapped userns cannot do: `chown` to unmapped ids
(no-op success — ownership is already correct inside the namespace;
fakeroot cannot substitute — its daemon propagates EINVAL) and `mknod`
of device nodes (placeholder regular files — the booted system mounts
devtmpfs). The squashfs forces 0:0 ownership (with `/home/demo` kept
1000:1000 via pseudo-file defs) because files created in the namespace
are owned by the host uid on disk.

```bash
# one-shot (needs a long-running shell):
packaging/live/build-live-iso-rootless.sh -o dist/nyrqis-live.iso

# two-phase, resumable (each phase fits a bounded window; the .deb
# cache survives interruptions, reruns cost extraction only):
packaging/live/build-live-iso-rootless.sh --acquire-rootfs /srv/lr
packaging/live/build-live-iso-rootless.sh --rootfs /srv/lr -o dist/nyrqis-live.iso
```

Requirements: `unshare` with unprivileged user namespaces enabled
(`sysctl kernel.unprivileged_userns_clone=1` on Debian; on Ubuntu 24.04+
check `apparmor_restrict_unprivileged_userns`), `cc` or `gcc`, and the
builder's normal toolset. Contract-pinned in
`source/nyhal-linux-backend/tests/test_rootless_build_contract.py`.

**arm64 works via this path too** (verified 2026-09-25: the rootless
arm64 ISO passed BOTH boot smokes on the reference machine — direct
ttyAMA0 handshake and the GRUB/UEFI menu path under
`qemu-system-aarch64 -M virt`):

```bash
packaging/live/build-live-iso-rootless.sh --arch arm64 \
    --acquire-rootfs ~/nyrqis-work/lr-arm64
packaging/live/build-live-iso-rootless.sh --arch arm64 \
    --rootfs ~/nyrqis-work/lr-arm64 -o dist/nyrqis-live-arm64.iso

# boot-smoke both paths (TCG — no KVM on arm64 here):
python3 tests/boot_smoke.py dist/nyrqis-live-arm64.iso \
    --arch arm64 --timeout 1680 --keep-logs
python3 tests/boot_smoke_menu.py dist/nyrqis-live-arm64.iso \
    --arch arm64 --timeout 1680 --keep-logs
```

The three extra ingredients, all rootless:

1. **A cross compiler for the guest shim.** The emulated debootstrap
   second stage and every emulated chroot step run AARCH64 binaries,
   whose loader refuses an amd64 preload — the shim is cross-compiled
   per build from the same committed C source. No `gcc-aarch64-linux-gnu`
   needed: [zig](https://ziglang.org/download) as a user-space tarball
   suffices (`zig cc -target aarch64-linux-gnu`); the driver probes
   `zig` on PATH, `~/.local/opt/zig/zig`, or `NYRQIS_ZIG`/`NYRQIS_AARCH64_CC`.
2. **binfmt_misc + qemu-user-static** (F-flagged registration, so the
   interpreter resolves inside chroots; check
   `ls /proc/sys/fs/binfmt_misc/qemu-aarch64`).
3. **`grub-efi-arm64`'s module tree** (`/usr/lib/grub/arm64-efi`) for
   `grub-mkrescue` — install the package where you can, or extract the
   .deb and unpack it to that path; the builder only needs the
   directory.

Notes: the driver keeps all its temp artifacts under
`~/.cache/nyrqis-rootless` (NOT /tmp — systemd-tmpfiles may empty /tmp
mid-build); the chroot wrapper is the shim class boundary (host amd64
shim outside, in-target aarch64 shim inside — the preload path is
staged on both sides so no loader ever warns); the cross build skips
Rust cdylibs of the host arch (`NYRQIS_CDYLIB_ARCH=aarch64`, the demo
honestly reports them unloaded); expect the emulated kernel package
configuration and `mkinitramfs` to dominate the build (~1 h total on
4 CPUs). CI's arm64 build is unaffected (runners build as root).

Got a prebuilt rootfs (CI produces one)? Skip debootstrap:

```bash
sudo packaging/live/build-live-iso.sh --rootfs /srv/nyrqis-rootfs -o out.iso
# or
sudo packaging/live/build-live-iso.sh --rootfs-tar rootfs.tar.zst -o out.iso
```

A prebuilt rootfs tree must contain: a `/boot/vmlinuz-*` +
`/boot/initrd.img-*` pair, `live-boot`, and enough userland for
`python3` + `bash` (everything Nyrqis-specific is copied in by the
builder). Since 0.29.13 the builder also **ensures the capability
probe's required packages** (`python3`, `python3-zstandard`,
`python3-nacl`, `python3-lz4`, `fuse3`) on every acquisition path and
runs a **probe-parity gate** before packing the squashfs: a build
fails rather than ship an image whose boot probe would print
`✗ MISSING:` for its own components. (`--skip-chroot` has no package
manager — supply a rootfs that already carries the packages.)

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

### Architecture support

Two architectures build, and **both are boot-smoked in CI**:

**amd64/x86_64 (default)** — hybrid UEFI + BIOS image: the rootfs
installs `linux-image-amd64`, boot images are `grub-pc-bin` +
`grub-efi-amd64-bin` + isolinux. Build with `build-live-iso.sh`, smoke
with `tests/boot_smoke.py` / `tests/boot_smoke_menu.py` (amd64 is the
drivers' default arch).

**arm64** (`build-live-iso.sh --arch arm64`) — UEFI-only image (there
is no BIOS/el torito/isolinux on arm64): `linux-image-arm64`, GRUB
`arm64-efi`, every menu entry carries `console=ttyAMA0,115200` (the
QEMU `virt` machine's UART). The rootfs cross-builds on an amd64 host
with a foreign debootstrap under `qemu-user-static` — no arm64 runner
needed. CI (`.github/workflows/live-iso-arm64.yml`) builds it weekly
and runs **both** boot smokes under `qemu-system-aarch64 -M virt`:

- direct kernel boot (`tests/boot_smoke.py --arch arm64`) over the
ttyAMA0 handshake;
- the human menu path (`tests/boot_smoke_menu.py --arch arm64`) through
the image's own GRUB, UEFI firmware supplied via `-bios`
(`qemu-efi-aarch64`'s edk2 image).

Real-hardware arm64 (Raspberry Pi 4/5, ARM servers) remains untested —
the QEMU-verified image is the deliverable until hardware smoke access
exists; do not claim Pi support beyond "boots in the virt machine".

Do not hand-wave an arm64 ISO as "hardware-ready": it is CI-boot-smoked
in QEMU only, and no release asset claims otherwise.

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

### Release uploads & the race harness

On a `v*` tag push, the `menu-boot` (amd64) and `menu-boot-arm64`
jobs each attach their ISO to the GitHub release. The attach logic —
race-safe create ("already exists" → re-view → upload proceeds),
bounded-curl upload with delete-before-upload idempotency, and the
draft self-heal (deleting a tag converts its release to a draft,
which hides every asset) — lives in ONE shared script:

```bash
scripts/attach_release_asset.sh   # the single source of truth
```

It is run by both workflows' attach steps, by their `re-attach`
workflow_dispatch jobs (manual recovery: re-attach the already-built
ISO from a run's artifact when `uploads.github.com` misbehaves —
dispatch on `main` with `release-tag=v…`), and by the race harness,
which runs it CONCURRENTLY against a fake `gh` + fake `curl` with
real concurrency semantics, in both race orders:

```bash
scripts/test_release_race.sh
# → round[amd64-loses]: amd64_rc=0 arm64_rc=0 assets_uploaded=2/2
# → round[arm64-loses]: amd64_rc=0 arm64_rc=0 assets_uploaded=2/2
# → round[rerun-sequential]: ... assets_replaced=1
# → round[rerun-clobber]: ... assets_replaced=1
# → RACE-HARNESS: ALL PASS
```

Run it after ANY change to `scripts/attach_release_asset.sh` (or when
touching `scripts/fake_gh.sh` / `scripts/fake_curl.sh`, which model
`gh`'s view/create/edit/api and curl's asset-upload behavior). Exit 0
= race-safe. Do NOT inline the attach logic back into the workflows —
that is how the harness drifted from the steps it claimed to test.

### Pipeline map (what runs in CI, and what each job proves)

The ISO pipeline is four jobs across two workflows — `live-iso.yml`
(amd64) and `live-iso-arm64.yml` (arm64). Each job is a named verdict:
a failure is diagnosable from the job list alone, and no job can mask
another.

| Job | Proves | Can fail alone because |
|-----|--------|------------------------|
| `build` (amd64) / `build-arm64` | the image is complete and internally consistent | debootstrap/copy/top-up steps, the dpkg audit gate, the probe-parity gate, the byte-compile gate, or the ISO size gate refused it; the **direct** smoke (in-job) then proves live-boot + daemon health, immune to bootloader problems |
| `menu-boot` / `menu-boot-arm64` | the **human** boot path works | GRUB menu, default entry, console wiring, or the UEFI firmware path broke — the direct smoke staying green isolates it to the bootloader |

**Gates inside the build job (fail closed, in order):**

1. **dpkg audit** — every rootfs acquisition path must have zero
   unpacked-but-unconfigured packages.
2. **Probe parity** — the boot probe's REQUIRED components
   (`python3`, `zstandard`, `nacl`, `lz4`, `fusermount3`, nyrqisctl,
   entry points) must be present in the image before it can ship.
3. **Byte-compile** — the shipped tree must compile with the image's
   own Python (bookworm = 3.11).
4. **ISO size** — the assembled ISO must be ≤ 500 MB (known-good
   ~354 MB). An oversized image almost always means a duplicated tree
   or stray artifact sneaked into the squashfs.

**Rootfs caching:** both workflows cache the debootstrap rootfs via
`actions/cache`, keyed by the include list — a package-set change
invalidates automatically. The chroot apt top-up runs on cache hits
too, so a cached rootfs can never predate a top-up change. First run
after a key change pays one bootstrap; routine rebuilds skip it.

**arm64 specifics:** the rootfs is cross-built (foreign debootstrap
under qemu-user + binfmt); GRUB arm64-efi modules come from the
Debian `.deb` (not apt-installable on amd64 runners); the menu job
boots through UEFI (`qemu-efi-aarch64`); the serial console is
ttyAMA0 — both smoke drivers and the demo's smoke handshake handle
it. Budget per arm64 boot: 900 s under TCG emulation.

**Triggering a run manually:** Actions → live-iso (or live-iso-arm64)
→ Run workflow. Every job carries an explicit `timeout-minutes`, so
a hung boot fails the job instead of burning the 6-hour default.
Serial logs upload as artifacts on failure (`if: always()`), and
failures emit `::error::` annotations readable through the API
without credentials.
