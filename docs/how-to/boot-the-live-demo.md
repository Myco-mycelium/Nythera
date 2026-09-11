# Boot the Nyrqis Live Demo ISO

One ISO, zero install: boot it and a full Nyrqis demo comes up — daemon,
desktop attempt, and a probe that lists **what is missing** on the machine
you booted. Full build/boot details live in
[`packaging/live/README.md`](https://github.com/Myco-mycelium/Nythera/blob/main/packaging/live/README.md);
this page is the operator's short version.

## Get an ISO

1. **Download from CI** (no local build): open the repository's Actions
   tab → the latest `live-iso` run → download the `nyrqis-live-iso`
   artifact. That image is not just built — CI **boots it headless**
   and verifies the daemon answers ping before the artifact is
   published (the serial log is uploaded alongside on any failure).
2. **Build locally**:

   ```bash
   sudo apt-get install -y debootstrap squashfs-tools genisoimage xorriso \
       grub-pc-bin grub-efi-amd64-bin mtools isolinux
   sudo packaging/live/build-live-iso.sh -o dist/nyrqis-live.iso
   ```

## Boot it

```bash
# VM (works without GPU passthrough — the compositor degrades honestly)
qemu-system-x86_64 -m 4G -enable-kvm -cdrom nyrqis-live.iso -boot d

# Real hardware (hybrid UEFI + BIOS image)
sudo dd if=nyrqis-live.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Pick the default `Nyrqis Live (demo)` entry; use the `verbose` entry when
diagnosing a boot problem, or `RAM` on machines with memory to spare.

## What you land in

The `demo` user is logged in on tty1 automatically and the `nyrqis-demo`
session prints:

- **Backend daemon** — started unprivileged on `/tmp/nyrqis-status.sock`,
  with the live `ep-limits list` shown inline.
- **Desktop session** — attempted automatically when a usable DRM device
  exists; otherwise the exact reason and the manual command.
- **Capability probe** — `✗ MISSING:` lines for every capability the
  machine lacks (DRM nodes, FUSE, user namespaces, zstandard / PyNaCl /
  lz4 modules, the prebuilt Rust serving-loop crate, …). That list is
  the deliverable: file it against the hardware matrix (M15 Phase 2) or
  a new issue.

## Drive the demo

```bash
python3 /opt/nyrqis/nyhal-linux-backend/nyrqisctl.py \
    --socket /tmp/nyrqis-status.sock ep-limits list
python3 /opt/nyrqis/nyhal-linux-backend/nyrqisctl.py \
    --socket /tmp/nyrqis-status.sock ep-limits metrics --watch 1
python3 /opt/nyrqis/nyhal-linux-backend/nyrqisctl.py \
    --socket /tmp/nyrqis-status.sock containers list
```

Re-run the banner + probe anytime with `nyrqis-demo`.

## References

- `packaging/live/README.md` — builder internals, prebuilt-rootfs path
- `.github/workflows/live-iso.yml` — the CI build + artifact upload
