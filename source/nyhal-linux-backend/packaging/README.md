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
└── packages/              # Installed packages

/run/nyrqis/               # Runtime state
├── status.sock            # Main IPC socket
├── health.sock            # Health probe socket
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
systemctl --user stop nyrqis-desktop

# Disable services
sudo systemctl disable nyrqis-backend
systemctl --user disable nyrqis-desktop

# Remove systemd units
sudo rm /etc/systemd/system/nyrqis-backend.service
sudo rm /etc/systemd/system/nyrqis-desktop.service
sudo systemctl daemon-reload

# Remove installed files
pip uninstall nyrqis-backend

# Remove data (optional)
sudo rm -rf /var/lib/nyrqis
rm -rf ~/.nyrqis ~/.config/nyrqis
```
