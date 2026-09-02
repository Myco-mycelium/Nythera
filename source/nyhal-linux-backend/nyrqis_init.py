#!/usr/bin/env python3
"""nyrqis_init — unified init script: boots the daemon, loads the shell, and
starts the desktop session.

This is the single entry point that brings up a complete Nyrqis desktop
environment from a cold boot.  It:

1. Starts the backend daemon (nyrqis_backend.py service serve) in the
   background and waits for the service socket to appear.
2. Loads a shell design (default: ~/.nyrqis/shell.nstudio) into the
   daemon via nyrqisctl nui load.
3. Starts the desktop session (nyrqis_session.py) which connects to the
   Wayland compositor (or falls back to headless/SDL2).

Usage:
    python3 nyrqis_init.py                        # full desktop boot
    python3 nyrqis_init.py --headless             # headless for CI
    python3 nyrqis_init.py --design /path/to.nstudio
    python3 nyrqis_init.py --daemon-only          # just start daemon
    python3 nyrqis_init.py --session-only         # just start session

References:
    - NPS-017 §4.5: boot and lifecycle
    - ADR-0026: Wayland display-server integration
    - NEXT_SESSION_PLAN.md: priorities
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nyrqis.init")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_SOCKET = "/tmp/nyrqis-status.sock"
DEFAULT_HEALTH_SOCKET = "/tmp/nyrqis-health.sock"
DEFAULT_STATE_DIR = os.path.expanduser("~/.nyrqis")
DEFAULT_DESIGN = os.path.join(DEFAULT_STATE_DIR, "shell.nstudio")
DEFAULT_VAULT_DIR = "/var/lib/nyrqis/vault"
DEFAULT_VAULT_KEY = ""
DEFAULT_COMMIT_INTERVAL = 5.0
SOCKET_WAIT_TIMEOUT = 10.0
SOCKET_POLL_INTERVAL = 0.1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_socket(path: str, timeout: float = SOCKET_WAIT_TIMEOUT) -> bool:
    """Poll until the daemon's service socket appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            # Try to connect to verify it's listening
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                sock.settimeout(0.5)
                sock.connect(path)
                sock.close()
                return True
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                pass
        time.sleep(SOCKET_POLL_INTERVAL)
    return False


def _find_design(user_design: Optional[str] = None) -> str:
    """Locate the shell design file."""
    if user_design and os.path.exists(user_design):
        return user_design

    candidates = [
        DEFAULT_DESIGN,
        os.path.join(os.path.dirname(__file__), "shell", "defaults",
                     "default-shell.nstudio"),
        os.path.join(os.path.dirname(__file__), "shell", "defaults",
                     "desktop.nstudio"),
        os.path.join(os.path.dirname(__file__), "tests", "fixtures",
                     "nstudio", "desktop.nstudio"),
        os.path.join(os.path.dirname(__file__), "tests", "fixtures",
                     "nstudio", "nyrqis-shell.nstudio"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def _socket_command(socket_path: str, payload: dict, timeout: float = 10.0) -> Optional[dict]:
    """Send a command to the daemon over the IPC transport and return the reply."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ipc.transport import IPCClient, DEFAULT_OPERATOR_ID

    tmp = tempfile.mkdtemp(prefix="nyrqis-init-")
    cli_path = os.path.join(tmp, "ctl.sock")
    client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
    try:
        reply = client.call(
            socket_path,
            json.dumps(payload).encode("utf-8"),
            timeout_s=timeout,
        )
        if reply is None:
            return None
        return json.loads(reply.payload.decode("utf-8"))
    except Exception as exc:
        logger.warning("socket command failed: %s", exc)
        return None
    finally:
        client.close()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Boot phases
# ---------------------------------------------------------------------------

def phase_start_daemon(
    socket_path: str,
    health_socket_path: str,
    vault_dir: str,
    vault_key_file: str,
    commit_interval: float,
    verbose: bool = False,
) -> Optional[subprocess.Popen]:
    """Start the backend daemon as a background process.

    Returns the Popen handle (caller must manage lifecycle).
    """
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    backend_script = os.path.join(backend_dir, "nyrqis_backend.py")

    cmd = [
        sys.executable, backend_script,
        "service", "serve",
        "--socket", socket_path,
        "--health-socket", health_socket_path,
        "--state-file", os.path.join(
            os.path.dirname(socket_path), "daemon-state.json"),
        "--vault-dir", vault_dir,
        "--commit-interval", str(commit_interval),
    ]
    if vault_key_file:
        cmd.extend(["--vault-key-file", vault_key_file])
    if verbose:
        cmd.append("--verbose")

    logger.info("Starting daemon: %s", " ".join(cmd))

    # Forward stdout/stderr to the parent process
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE if not verbose else None,
        stderr=subprocess.PIPE if not verbose else None,
    )

    logger.info("Daemon PID: %d", process.pid)
    return process


def phase_wait_for_daemon(socket_path: str) -> bool:
    """Wait for the daemon to be ready."""
    logger.info("Waiting for daemon on %s ...", socket_path)
    if _wait_for_socket(socket_path):
        logger.info("Daemon is ready")
        return True
    logger.error("Daemon did not become ready within %.0fs", SOCKET_WAIT_TIMEOUT)
    return False


def phase_load_shell(socket_path: str, design_path: str) -> bool:
    """Load a shell design into the daemon."""
    if not design_path:
        logger.warning("No shell design found; skipping load")
        return True

    logger.info("Loading shell design: %s", design_path)
    payload = {
        "service": "nui",
        "op": "nui_load",
        "document": design_path,
    }
    reply = _socket_command(socket_path, payload)
    if reply is None:
        logger.error("Failed to load shell design (no reply)")
        return False
    if reply.get("ok"):
        logger.info("Shell design loaded successfully")
        return True
    else:
        logger.error("Failed to load shell design: %s", reply.get("error", "unknown"))
        return False


def phase_start_session(
    design_path: str,
    socket_path: str,
    headless: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
    theme: Optional[str] = None,
    verbose: bool = False,
) -> int:
    """Start the desktop session (blocking)."""
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    session_script = os.path.join(backend_dir, "nyrqis_session.py")

    cmd = [sys.executable, session_script, design_path]
    if headless:
        cmd.append("--headless")
    if width:
        cmd.extend(["--width", str(width)])
    if height:
        cmd.extend(["--height", str(height)])
    if theme:
        cmd.extend(["--theme", theme])
    if verbose:
        cmd.append("--verbose")

    logger.info("Starting session: %s", " ".join(cmd))
    return subprocess.call(cmd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="nyrqis_init",
        description="Unified Nyrqis init: boots daemon + loads shell + starts desktop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 nyrqis_init.py                        # full desktop boot
    python3 nyrqis_init.py --headless             # headless (CI/testing)
    python3 nyrqis_init.py --design shell.nstudio # custom design
    python3 nyrqis_init.py --daemon-only          # just the daemon
    python3 nyrqis_init.py --session-only         # just the session
        """,
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--socket", default=DEFAULT_SOCKET,
        help=f"Daemon IPC socket path (default: {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--health-socket", default=DEFAULT_HEALTH_SOCKET,
        help=f"Health probe socket (default: {DEFAULT_HEALTH_SOCKET})",
    )
    parser.add_argument(
        "--design", default=None,
        help="Shell design file (.nstudio)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run headless (no window, render one frame)",
    )
    parser.add_argument(
        "--width", type=int, default=None,
        help="Session window width",
    )
    parser.add_argument(
        "--height", type=int, default=None,
        help="Session window height",
    )
    parser.add_argument(
        "--theme", default=None,
        help="Theme override (Eclipse, Solar)",
    )
    parser.add_argument(
        "--vault-dir", default=DEFAULT_VAULT_DIR,
        help="Vault backing directory",
    )
    parser.add_argument(
        "--vault-key-file", default=DEFAULT_VAULT_KEY,
        help="Vault encryption key envelope",
    )
    parser.add_argument(
        "--commit-interval", type=float, default=DEFAULT_COMMIT_INTERVAL,
        help="Deferred write commit interval (seconds)",
    )
    parser.add_argument(
        "--daemon-only", action="store_true",
        help="Only start the daemon (don't start session)",
    )
    parser.add_argument(
        "--session-only", action="store_true",
        help="Only start the session (don't start daemon)",
    )
    parser.add_argument(
        "--no-daemon", action="store_true",
        help="Don't start daemon; assume it's already running",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║         Nyrqis Init — Boot Sequence       ║")
    logger.info("╚══════════════════════════════════════════╝")

    # Find the shell design
    design_path = _find_design(args.design)
    if not design_path:
        logger.warning("No shell design found; session will start with defaults")

    daemon_process: Optional[subprocess.Popen] = None
    exit_code = 0

    try:
        # ── Phase 1: Start the daemon ────────────────────────────
        if not args.session_only and not args.no_daemon:
            logger.info("── Phase 1: Starting backend daemon ──")
            daemon_process = phase_start_daemon(
                socket_path=args.socket,
                health_socket_path=args.health_socket,
                vault_dir=args.vault_dir,
                vault_key_file=args.vault_key_file,
                commit_interval=args.commit_interval,
                verbose=args.verbose,
            )

            if not phase_wait_for_daemon(args.socket):
                logger.error("Daemon failed to start")
                return 1

        # ── Phase 2: Load the shell design ──────────────────────
        if not args.session_only and design_path:
            logger.info("── Phase 2: Loading shell design ──")
            if not phase_load_shell(args.socket, design_path):
                logger.warning("Shell design load failed; continuing anyway")

        if args.daemon_only:
            logger.info("Daemon-only mode; waiting for signal")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Interrupted")
            return 0

        # ── Phase 3: Start the desktop session ──────────────────
        logger.info("── Phase 3: Starting desktop session ──")
        if not design_path:
            logger.error("No shell design available for session")
            return 1

        exit_code = phase_start_session(
            design_path=design_path,
            socket_path=args.socket,
            headless=args.headless,
            width=args.width,
            height=args.height,
            theme=args.theme,
            verbose=args.verbose,
        )

    except KeyboardInterrupt:
        logger.info("Interrupted during boot")
        exit_code = 130
    except Exception as exc:
        logger.exception("Boot failed: %s", exc)
        exit_code = 1
    finally:
        # Clean up daemon
        if daemon_process is not None:
            logger.info("Stopping daemon (PID %d)...", daemon_process.pid)
            daemon_process.send_signal(signal.SIGTERM)
            try:
                daemon_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Daemon did not stop; sending SIGKILL")
                daemon_process.kill()
            logger.info("Daemon stopped")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
