#!/usr/bin/env bash
# Nyrqis DRM setup script
#
# Sets up DRM device access for the current user without root privileges.
# Installs udev rules, creates the 'nyrqis' group, and adds the user to it.
#
# Usage:
#   ./setup-drm.sh              # Interactive setup
#   ./setup-drm.sh --check      # Check current status only
#   ./setup-drm.sh --install    # Install udev rules (requires sudo)
#
# After running, log out and back in for group changes to take effect.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UDEV_RULES="${SCRIPT_DIR}/udev/90-nyrqis-drm.rules"
NYRQIS_GROUP="nyrqis"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_status() {
    echo "=== Nyrqis DRM Setup Status ==="
    echo ""

    # Check if nyrqis group exists
    if getent group "$NYRQIS_GROUP" >/dev/null 2>&1; then
        info "Group '$NYRQIS_GROUP' exists"
    else
        warn "Group '$NYRQIS_GROUP' does not exist"
    fi

    # Check if current user is in the group
    if groups "$USER" 2>/dev/null | grep -q "$NYRQIS_GROUP"; then
        info "User '$USER' is in group '$NYRQIS_GROUP'"
    else
        warn "User '$USER' is NOT in group '$NYRQIS_GROUP'"
    fi

    # Check udev rules
    if [ -f /etc/udev/rules.d/90-nyrqis-drm.rules ]; then
        info "udev rules installed at /etc/udev/rules.d/"
    else
        warn "udev rules NOT installed"
    fi

    # Check DRM device permissions
    echo ""
    echo "=== DRM Device Permissions ==="
    for dev in /dev/dri/renderD* /dev/dri/card*; do
        if [ -e "$dev" ]; then
            perms=$(stat -c "%a %U %G" "$dev" 2>/dev/null || echo "unknown")
            echo "  $dev: $perms"
        fi
    done

    # Test access
    echo ""
    echo "=== Access Test ==="
    for dev in /dev/dri/renderD*; do
        if [ -e "$dev" ]; then
            if python3 -c "import os; fd = os.open('$dev', os.O_RDWR); os.close(fd)" 2>/dev/null; then
                info "Can open $dev"
            else
                warn "Cannot open $dev"
            fi
        fi
    done
}

install_rules() {
    echo "=== Installing Nyrqis DRM udev rules ==="

    # Check for sudo
    if ! command -v sudo &>/dev/null; then
        error "sudo is required for installation"
        exit 1
    fi

    # Create nyrqis group if it doesn't exist
    if ! getent group "$NYRQIS_GROUP" >/dev/null 2>&1; then
        info "Creating group '$NYRQIS_GROUP'..."
        sudo groupadd "$NYRQIS_GROUP"
    fi

    # Add current user to the group
    if ! groups "$USER" 2>/dev/null | grep -q "$NYRQIS_GROUP"; then
        info "Adding user '$USER' to group '$NYRQIS_GROUP'..."
        sudo usermod -aG "$NYRQIS_GROUP" "$USER"
    fi

    # Install udev rules
    if [ -f "$UDEV_RULES" ]; then
        info "Installing udev rules..."
        sudo cp "$UDEV_RULES" /etc/udev/rules.d/
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        info "udev rules installed"
    else
        error "udev rules file not found: $UDEV_RULES"
        exit 1
    fi

    echo ""
    info "Setup complete!"
    echo ""
    echo "  1. Log out and back in for group changes to take effect"
    echo "  2. Run '$0 --check' to verify access"
    echo "  3. Test with: python3 -c \"import os; fd = os.open('/dev/dri/renderD128', os.O_RDWR); os.close(fd)\""
}

case "${1:-}" in
    --check)
        check_status
        ;;
    --install)
        install_rules
        ;;
    --help|-h)
        echo "Usage: $0 [--check|--install|--help]"
        echo ""
        echo "Options:"
        echo "  --check     Check current DRM setup status"
        echo "  --install   Install udev rules (requires sudo)"
        echo "  --help      Show this help"
        ;;
    "")
        check_status
        ;;
    *)
        error "Unknown option: $1"
        echo "Use --help for usage"
        exit 1
        ;;
esac
