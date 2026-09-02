#!/usr/bin/env bash
# Nyrqis Linux Backend installation script
#
# Usage:
#   sudo ./install.sh              # system-wide install
#   ./install.sh --user            # user-local install
#   ./install.sh --dev             # development install (editable)
#
# References:
#   - BUILD_ARCHITECTURE.md: toolchain requirements
#   - packaging/README.md: installation guide

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BACKEND_DIR")"

# Defaults
INSTALL_MODE="system"
PREFIX="/usr/local"
PYTHON="${PYTHON:-python3}"
VENV_DIR=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            INSTALL_MODE="user"
            PREFIX="${HOME}/.local"
            shift
            ;;
        --dev|--editable)
            INSTALL_MODE="dev"
            shift
            ;;
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [--user|--dev|--prefix PATH] [--python PATH]"
            echo ""
            echo "Options:"
            echo "  --user       Install to ~/.local (no sudo required)"
            echo "  --dev        Editable/development install"
            echo "  --prefix     Install prefix (default: /usr/local)"
            echo "  --python     Python interpreter (default: python3)"
            echo "  --help       Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

echo "╔══════════════════════════════════════════╗"
echo "║     Nyrqis Linux Backend Installer       ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Mode:       ${INSTALL_MODE}"
echo "  Prefix:     ${PREFIX}"
echo "  Python:     ${PYTHON}"
echo "  Backend:    ${BACKEND_DIR}"
echo ""

# Check Python version
PYTHON_VERSION=$("${PYTHON}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 10 ]]; then
    echo "ERROR: Python >= 3.10 required (found ${PYTHON_VERSION})" >&2
    exit 1
fi
echo "✓ Python ${PYTHON_VERSION}"

# Check system dependencies
echo ""
echo "Checking system dependencies..."

check_dep() {
    local name="$1"
    local pkg="$2"
    if command -v "$name" &>/dev/null; then
        echo "  ✓ $name"
        return 0
    elif dpkg -s "$pkg" &>/dev/null 2>&1; then
        echo "  ✓ $name (package: $pkg)"
        return 0
    else
        echo "  ⚠ $name not found (install $pkg for full functionality)"
        return 1
    fi
}

HAS_FUSE=false
HAS_SDL2=false

check_dep "fusermount" "fuse" && HAS_FUSE=true
check_dep "sdl2-config" "libsdl2-dev" && HAS_SDL2=true
check_dep "wayland-info" "wayland-utils" || true

if [[ "$HAS_FUSE" == "false" ]]; then
    echo ""
    echo "  NOTE: FUSE is required for NyFS filesystem mounts."
    echo "  Install: sudo apt install fuse libfuse-dev fuse3"
fi

if [[ "$HAS_SDL2" == "false" ]]; then
    echo ""
    echo "  NOTE: SDL2 is required for the desktop session."
    echo "  Install: sudo apt install libsdl2-dev libsdl2-image-dev"
fi

# Install based on mode
echo ""
echo "Installing..."

case "$INSTALL_MODE" in
    system)
        echo "Installing Python packages to ${PREFIX}..."
        "${PYTHON}" -m pip install --prefix="${PREFIX}" "${BACKEND_DIR}"
        echo ""
        echo "Installing systemd units..."
        if [[ -d /etc/systemd/system ]]; then
            cp "${SCRIPT_DIR}/systemd/nyrqis-backend.service" /etc/systemd/system/
            cp "${SCRIPT_DIR}/systemd/nyrqis-desktop.service" /etc/systemd/system/
            systemctl daemon-reload
            echo "  ✓ systemd units installed"
            echo "  To enable: sudo systemctl enable nyrqis-backend"
            echo "  To start:  sudo systemctl start nyrqis-backend"
        else
            echo "  ⚠ systemd not found — copy units manually:"
            echo "    ${SCRIPT_DIR}/systemd/nyrqis-backend.service → /etc/systemd/system/"
            echo "    ${SCRIPT_DIR}/systemd/nyrqis-desktop.service → /etc/systemd/system/"
        fi
        echo ""
        echo "Creating directories..."
        mkdir -p /var/lib/nyrqis/vault
        mkdir -p /var/lib/nyrqis/packages
        mkdir -p /run/nyrqis
        echo "  ✓ /var/lib/nyrqis/vault"
        echo "  ✓ /var/lib/nyrqis/packages"
        echo "  ✓ /run/nyrqis"
        ;;
    user)
        echo "Installing Python packages to ${PREFIX}..."
        "${PYTHON}" -m pip install --user --prefix="${PREFIX}" "${BACKEND_DIR}"
        echo ""
        echo "Creating user directories..."
        mkdir -p "${HOME}/.nyrqis"
        mkdir -p "${HOME}/.local/share/nyrqis"
        mkdir -p "${HOME}/.config/nyrqis"
        echo "  ✓ ~/.nyrqis"
        echo "  ✓ ~/.local/share/nyrqis"
        echo "  ✓ ~/.config/nyrqis"
        echo ""
        # Install default shell design if not present
        if [[ ! -f "${HOME}/.nyrqis/shell.nstudio" ]]; then
            DEFAULT_SHELL="${BACKEND_DIR}/shell/defaults/default-shell.nstudio"
            if [[ -f "$DEFAULT_SHELL" ]]; then
                cp "$DEFAULT_SHELL" "${HOME}/.nyrqis/shell.nstudio"
                echo "  ✓ Default shell design installed to ~/.nyrqis/shell.nstudio"
            fi
        else
            echo "  → Shell design already exists at ~/.nyrqis/shell.nstudio"
        fi
        echo ""
        # Install default shell design if not present
        if [[ ! -f "${HOME}/.nyrqis/shell.nstudio" ]]; then
            DEFAULT_SHELL="${BACKEND_DIR}/shell/defaults/default-shell.nstudio"
            if [[ -f "$DEFAULT_SHELL" ]]; then
                cp "$DEFAULT_SHELL" "${HOME}/.nyrqis/shell.nstudio"
                echo "  ✓ Default shell design installed to ~/.nyrqis/shell.nstudio"
            fi
        else
            echo "  → Shell design already exists at ~/.nyrqis/shell.nstudio"
        fi
        echo ""
        echo "To enable the user service:"
        echo "  systemctl --user enable nyrqis-backend"
        echo "  systemctl --user start nyrqis-backend"
        ;;
    dev)
        echo "Installing in development (editable) mode..."
        "${PYTHON}" -m pip install --editable "${BACKEND_DIR}[dev,crypto,ui]"
        echo ""
        echo "Creating user directories..."
        mkdir -p "${HOME}/.nyrqis"
        mkdir -p "${HOME}/.local/share/nyrqis"
        mkdir -p "${HOME}/.config/nyrqis"
        echo "  ✓ ~/.nyrqis"
        echo "  ✓ ~/.local/share/nyrqis"
        echo "  ✓ ~/.config/nyrqis"
        echo ""
        # Install default shell design if not present
        if [[ ! -f "${HOME}/.nyrqis/shell.nstudio" ]]; then
            DEFAULT_SHELL="${BACKEND_DIR}/shell/defaults/default-shell.nstudio"
            if [[ -f "$DEFAULT_SHELL" ]]; then
                cp "$DEFAULT_SHELL" "${HOME}/.nyrqis/shell.nstudio"
                echo "  ✓ Default shell design installed to ~/.nyrqis/shell.nstudio"
            fi
        else
            echo "  → Shell design already exists at ~/.nyrqis/shell.nstudio"
        fi
        ;;
esac

# Build Rust crates if cargo is available
echo ""
if command -v cargo &>/dev/null; then
    echo "Building Rust crates..."
    CARGO_DIR="${BACKEND_DIR}/rust"
    if [[ -d "$CARGO_DIR" ]]; then
        for crate_dir in "${CARGO_DIR}"/*/; do
            if [[ -f "${crate_dir}/Cargo.toml" ]]; then
                crate_name=$(basename "$crate_dir")
                echo -n "  Building ${crate_name}... "
                if (cd "$crate_dir" && cargo build --release 2>/dev/null); then
                    echo "✓"
                else
                    echo "⚠ (build failed, crate-less fallback active)"
                fi
            fi
        done
    fi
else
    echo "⚠ cargo not found — Rust crates not built"
    echo "  Install Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    echo "  The backend runs with Python fallback when crates are absent."
fi

# Post-install verification
echo ""
echo "Running post-install verification..."
if "${PYTHON}" -c "from backend.container import ContainerManager; print('  ✓ Backend imports OK')" 2>/dev/null; then
    :
else
    echo "  ⚠ Backend imports failed — check installation"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         Installation Complete             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Quick start:"
echo "  nyrqis-backend boot                    # Boot the system"
echo "  nyrqisctl status                       # Check daemon status"
echo "  nyrqisctl containers list              # List containers"
echo "  nyrqis-session shell.nstudio           # Start desktop session"
echo ""
echo "Service management:"
echo "  sudo systemctl start nyrqis-backend    # Start daemon"
echo "  sudo systemctl status nyrqis-backend   # Check status"
echo "  journalctl -u nyrqis-backend -f        # Follow logs"
echo ""
