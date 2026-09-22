# Nyrqis Linux Backend — Packaging

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.10 | 3.12+ |
| Rust | 1.75 | stable |
| Linux kernel | 5.10 | 6.1+ |
| RAM | 512 MB | 2 GB |
| Disk | 100 MB | 1 GB |

### System Dependencies

```bash
# Ubuntu/Debian
sudo apt install -y \
    python3-dev python3-pip python3-venv \
    fuse3 libfuse-dev \
    libseccomp-dev \
    libsdl2-dev libsdl2-image-dev \
    libwayland-dev wayland-protocols

# Fedora/RHEL
sudo dnf install -y \
    python3-devel python3-pip \
    fuse3 fuse3-devel \
    libseccomp-devel \
    SDL2-devel SDL2_image-devel \
    wayland-devel wayland-protocols

# Arch
sudo pacman -S \
    python python-pip \
    fuse3 fuse3 \
    libseccomp \
    sdl2 sdl2_image \
    wayland wayland-protocols
```

## Installation

### System-wide (requires root)

```bash
cd source/nyhal-linux-backend
sudo ./packaging/install.sh
```

### User-local (no root)

```bash
cd source/nyhal-linux-backend
./packaging/install.sh --user
```

### Development (editable)

```bash
cd source/nyhal-linux-backend
./packaging/install.sh --dev
```

## Systemd Integration

### Backend Service

The backend daemon runs as a system service:

```bash
# Enable and start
sudo systemctl enable nyrqis-backend
sudo systemctl start nyrqis-backend

# Status and logs
sudo systemctl status nyrqis-backend
journalctl -u nyrqis-backend -f

# Stop/restart
sudo systemctl stop nyrqis-backend
sudo systemctl restart nyrqis-backend
```

The systemd tree (`packaging/systemd/`) ships three units:
`nyrqis-backend.service`, `nyrqis-pki.service`, and
`nyrqis-desktop.service` — the backend tree's copies are the source of
truth and `install.sh` deploys all three (`systemd-analyze verify`
clean; the two-tree mirror rule is contract-pinned in
`tests/test_package_pki.py`).

### Desktop Session

The desktop session runs as a user service:

```bash
# Enable and start (as your user)
systemctl --user enable nyrqis-desktop
systemctl --user start nyrqis-desktop

# Status
systemctl --user status nyrqis-desktop
journalctl --user -u nyrqis-desktop -f
```

### Package-PKI Daemon (NPS-028)

The PKI daemon serves the package trust store: the §3.4 custody store
(ADR-0023 envelope encryption), the §7 tamper-evident audit chain, and
the §3.2 authority-guarded IPC socket (`/run/nyrqis/pki.sock`, 0600).
Custody is mandatory: without an unlock secret the daemon exits.

```bash
# One-time: create the unlock secret (0700 dir, 0600 file)
sudo mkdir -p /etc/nyrqis && sudo chmod 0700 /etc/nyrqis
echo 'NYRQIS_PKI_UNLOCK_SECRET=change-me' | sudo tee /etc/nyrqis/pki.env >/dev/null
sudo chmod 0600 /etc/nyrqis/pki.env

# Enable and start
sudo systemctl enable --now nyrqis-pki

# Status and logs
sudo systemctl status nyrqis-pki
journalctl -u nyrqis-pki -f
```

The store (`/var/lib/nyrqis/pki/store.custody.json`) and the audit
chain (`/var/lib/nyrqis/pki/audit.jsonl`) persist across restarts;
enrollments, revocations, and audit entries survive a reboot.

## Configuration

### Backend Configuration

The backend daemon accepts these configuration options:

| Option | Default | Description |
|--------|---------|-------------|
| `--socket` | `/tmp/nyrqis-status.sock` | Main IPC socket |
| `--health-socket` | (disabled) | Health probe socket |
| `--state-file` | `/run/nyrqis/daemon-state.json` | Persistent state |
| `--vault-dir` | `/var/lib/nyrqis/vault` | Storage vault directory |
| `--vault-key-file` | (disabled) | Encryption key envelope |
| `--commit-interval` | `5.0` | Deferred write interval (s) |
| `--syslog` | (disabled) | Mirror logs to system journal |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `NYRQIS_VAULT_PASSPHRASE` | Vault unlock passphrase |
| `NYRQIS_RUST_LIB` | Override Rust crate path |
| `NYRQIS_RUST_FORCE` | Force Rust crate usage (`1`) |
| `DISPLAY` | X11 display for SDL2 |
| `WAYLAND_DISPLAY` | Wayland compositor name |

## Directory Layout

After installation:

```
/var/lib/nyrqis/           # System data
├── vault/                 # Encrypted vault volumes
│   └── ...
├── packages/              # Installed packages
└── pki/                   # Package trust store (NPS-028)
    ├── store.custody.json # §3.4 custody store (envelope-encrypted)
    └── audit.jsonl        # §7 tamper-evident audit chain

/run/nyrqis/               # Runtime state
├── status.sock            # Main IPC socket
├── health.sock            # Health probe socket
├── pki.sock               # §3.2 authority-guarded PKI IPC socket
└── daemon-state.json      # Daemon identity

~/.nyrqis/                 # User data
├── shell.nstudio          # Loaded shell design
└── ...

~/.config/nyrqis/          # User configuration
└── ...
```

## Uninstalling

```bash
# Stop services
sudo systemctl stop nyrqis-backend
sudo systemctl stop nyrqis-pki
systemctl --user stop nyrqis-desktop

# Disable services
sudo systemctl disable nyrqis-backend
sudo systemctl disable nyrqis-pki
systemctl --user disable nyrqis-desktop

# Remove systemd units
sudo rm /etc/systemd/system/nyrqis-backend.service
sudo rm /etc/systemd/system/nyrqis-pki.service
sudo rm /etc/systemd/system/nyrqis-desktop.service
sudo systemctl daemon-reload

# Remove installed files
pip uninstall nyrqis-backend

# Remove data (optional)
sudo rm -rf /var/lib/nyrqis
rm -rf ~/.nyrqis ~/.config/nyrqis
```
