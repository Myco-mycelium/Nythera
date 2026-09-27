# The Nyrqis Live Demo — ISO Boot Path

The host-side scripted demo (`demo/run_demo.sh`) exercises the
operator surface against a daemon on a running OS. The **live ISO**
packages the same platform into a bootable image: it autologs in as
the `demo` user, starts the daemon, prints a guided banner with a
capability probe, and drops you at a shell with the full CLI
available. This guide is the demo path for the ISO.

## Boot it (QEMU, x86_64)

```bash
qemu-system-x86_64 \
    -m 2048 -enable-kvm \
    -cdrom nyrqis-live.iso -boot d -net none
```

- `-net none` is the honest default: the live platform performs **no
  network egress**, and the demo is designed to prove everything on an
  air-gapped machine.
- For a headless/serial view (or on a server): add `-nographic`. The
  demo session mirrors to the serial console (`ttyS0` on amd64,
  `ttyAMA0` on arm64).
- arm64 image: `qemu-system-aarch64 -M virt -cpu cortex-a57 -m 2048
  -nographic -cdrom nyrqis-live-arm64.iso` (UEFI path; see the GRUB
  menu entry too).

The `demo` user is autologged in on tty1 (and the serial getty); the
session starts the daemon **unprivileged** (user-namespace posture —
never root) and prints the banner, the capability probe (exactly what
this machine is missing), and the boot-smoke handshake markers.

## The guided tour, on the ISO

Once at the shell, the same five-act story as the host demo plays
manually. The tour, with the exact commands:

### 1. Bring-up (already done by the session)

```bash
nyrqisctl ping
nyrqisctl status
```

The session starts the daemon on `/tmp/nyrqis-status.sock` and adopts
it across both consoles (the flock singleton). `NYRQIS_BOOT_SMOKE_PONG=1`
in the serial log is the machine-readable version of this.

### 2. Containers

```bash
nyrqisctl containers list
```

Capability-scoped, unprivileged (user namespaces) — the default
`memory_mb=256` / `pid_limit=64` posture is shipped in the daemon.

### 3. The signed-package update cycle (UPD-001 Option A)

The image ships the new `packages` command family. A self-contained
tour, entirely on the live machine:

```bash
cd /opt/nyrqis/nyhal-linux-backend
python3 - <<'EOF'
# Build a tiny signed repo + install v1 (the demo publisher).
import json, shutil, sys
sys.path.insert(0, ".")
from backend import package_repo, package_signing, delta_update
from pathlib import Path
t = Path("/tmp/demo-pkg"); shutil.rmtree(t, ignore_errors=True)
kp = package_signing.SigningKeypair.generate()
v1 = t/"v1"; v1.mkdir(parents=True); (v1/"app.bin").write_bytes(b"DEMO-v1")
v2 = t/"v2"; v2.mkdir(parents=True); (v2/"app.bin").write_bytes(b"DEMO-v2")
repo = package_repo.PackageRepository(str(t/"repo"))
repo.publish_package(str(v1), "demo", "1.0.0", kp)
d = delta_update.create_delta_update(str(v1), str(v2), "demo", "1.0.0", "1.1.0", signing_keypair=kp)
delta_update.save_delta_update(d, str(t/"demo.delta"))
repo.publish_delta(d, str(t/"demo.delta"), kp)
import base64
(t/"trust.json").write_text(json.dumps({"trusted_keys": [{
    "key_id": kp.fingerprint,
    "public_key": base64.b64encode(kp.public_key).decode()}]}))
shutil.copytree(v1, t/"install"/"demo")
(t/"install"/"demo"/"manifest.json").write_text('{"package_id":"demo","version":"1.0.0"}')
print("repo ready at", t)
EOF

nyrqisctl packages verify  --repo-root /tmp/demo-pkg/repo --trust-store /tmp/demo-pkg/trust.json
nyrqisctl packages update demo --repo-root /tmp/demo-pkg/repo --trust-store /tmp/demo-pkg/trust.json --install-root /tmp/demo-pkg/install --state-dir /tmp/demo-pkg/state
cat /tmp/demo-pkg/install/demo/app.bin          # DEMO-v2
nyrqisctl packages rollback demo 1.1.0 --repo-root /tmp/demo-pkg/repo --trust-store /tmp/demo-pkg/trust.json --install-root /tmp/demo-pkg/install --state-dir /tmp/demo-pkg/state
cat /tmp/demo-pkg/install/demo/app.bin          # DEMO-v1 (restored)
```

### 4. The local crash spool (CRY-001 Option A)

Restart the daemon with spooling enabled; the §4.5 recovery path spools
a redacted report for the previous daemon:

```bash
pkill -f 'service serve' ; sleep 1
nohup python3 /opt/nyrqis/nyhal-linux-backend/nyrqis_backend.py \
    service serve --socket /tmp/nyrqis-status.sock \
    --state-file /tmp/nyrqis-daemon-state.json \
    --vault-dir /tmp/nyrqis-vault \
    --crash-spool /tmp/nyrqis-crash-spool >/tmp/nyrqis-daemon.log 2>&1 &
sleep 3
nyrqisctl crash list --spool-dir /tmp/nyrqis-crash-spool
nyrqisctl crash show <report-id> --spool-dir /tmp/nyrqis-crash-spool
```

The report carries `redacted: true` — vault aggregates never reach the
spooled bytes, and nothing left the machine (the no-egress property is
contract-pinned, not prose).

### 5. Diagnostics

```bash
nyrqisctl debug bundle --out /tmp/incident-bundle
cat /tmp/incident-bundle/meta.json
```

### 6. What is missing here (the honest probe)

```bash
nyrqis-demo --probe-only
```

prints exactly what this hardware lacks (DRM nodes, fusermount3, KVM,
…) — the machine-dependent gaps are reported, never hidden.

## The automated boot smokes

The CI drivers exercise the same path headlessly (no human, assertions
on the serial log):

```bash
python3 tests/boot_smoke.py --iso /path/to/nyrqis-live.iso            # direct boot
python3 tests/boot_smoke_menu.py --iso /path/to/nyrqis-live.iso       # GRUB menu path
python3 tests/boot_smoke.py --arch arm64 --iso /path/to/nyrqis-live-arm64.iso
```

Pass = `NYRQIS_BOOT_SMOKE_PONG=1`, `NYRQIS_BOOT_SMOKE_PKGS=ok`,
`NYRQIS_BOOT_SMOKE_READY=1` in the serial log (and the menu driver's
GRUB assertions). These are the same checks the release workflow runs.

## Presenter notes

- The demo user is unprivileged by design; everything survives a
  reboot because nothing persists — the ISO is immutable, which is
  itself a talking point (the platform's "platform update = re-imaging"
  scope answer, UPD-001 §5 Q2).
- If the desktop attempt reports it cannot present (VM without
  virtio-gpu), that is the honest probe working; the console demo is
  unaffected.
- Everything degrades honestly: a missing PyNaCl disables signing
  (fail-closed, visibly), a missing DRM node disables scanout (software
  frames), a missing Rust cdylib falls back to the Python floor.
