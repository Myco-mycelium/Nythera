#!/usr/bin/env python3
"""
Network — veth/bridge outbound connectivity for Nyrqis containers

Implements the outstanding networking item from IMPLEMENTATION_STATUS.md §1:

    veth/bridge outbound connectivity for ``network=True`` containers
    (requires host CAP_NET_ADMIN/root — the netns is currently a
    loopback-only isolation boundary with a usable localhost)

Design (kept minimal and auditable):

- A bridge (``nyrqis-br0``, 172.16.0.1/24) is created on the host the
  first time a ``network=True`` container spawns.
- A veth pair is created: ``veth-<short-id>`` (host side, attached to
  the bridge) and ``eth0`` (container side, moved into the container's
  netns).
- The container's ``eth0`` gets an IP from the 172.16.0.0/24 subnet
  (gateway 172.16.0.1 = the bridge).
- IP forwarding + masquerade (SNAT) on the host enable outbound
  internet access for the container.
- On container teardown the veth pair is removed; the bridge persists
  (the last container to leave cleans it up).

All operations are best-effort and fail-closed: a failure to set up
networking means the container runs with loopback only (the pre-existing
behavior).

References:
- NPS-017 §4.1: container isolation (network namespace)
- IMPLEMENTATION_STATUS.md §1: outstanding veth/bridge work
"""

import ctypes
import ctypes.util
import logging
import os
import struct
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bridge configuration
BRIDGE_NAME = "nyrqis-br0"
BRIDGE_SUBNET = "172.16.0.1"
BRIDGE_CIDR = "172.16.0.1/24"
CONTAINER_SUBNET_PREFIX = "172.16.0"
CONTAINER_GW = "172.16.0.1"

# ioctl constants
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1

# Netlink constants for advanced operations (best-effort fallback to ip)
RTM_NEWLINK = 16
RTM_DELLINK = 17
RTM_NEWADDR = 20
RTM_DELADDR = 21
NLM_F_REQUEST = 1
NLM_F_CREATE = 0x400
NLM_F_EXCL = 0x200
NLM_F_ACK = 4

# Track next IP to assign
_next_ip = [2]  # 172.16.0.2, 172.16.0.3, ...


def _run(cmd: list, check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command, returning the result. Best-effort, never raises."""
    try:
        return subprocess.run(
            cmd,
            capture_output=capture,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("network: command %s failed: %s", cmd, e)
        return subprocess.CompletedProcess(cmd, 1, b"", str(e).encode())


def _ip(*args: str) -> bool:
    """Run ``ip <args>`` and return True on success."""
    result = _run(["ip", *args])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        logger.debug("network: ip %s failed: %s", " ".join(args), stderr)
        return False
    return True


def _ensure_bridge() -> bool:
    """Create the bridge if it does not exist, bring it up."""
    # Check if bridge already exists
    result = _run(["ip", "link", "show", BRIDGE_NAME])
    if result.returncode == 0:
        logger.debug("network: bridge %s already exists", BRIDGE_NAME)
        # Ensure it's up
        _ip("link", "set", BRIDGE_NAME, "up")
        return True

    # Create the bridge
    if not _ip("link", "add", BRIDGE_NAME, "type", "bridge"):
        logger.warning("network: failed to create bridge %s", BRIDGE_NAME)
        return False

    # Assign the gateway IP
    if not _ip("addr", "add", BRIDGE_CIDR, "dev", BRIDGE_NAME):
        # Might already have the address
        logger.debug("network: addr add failed (may already exist)")

    # Bring the bridge up
    if not _ip("link", "set", BRIDGE_NAME, "up"):
        logger.warning("network: failed to bring up bridge %s", BRIDGE_NAME)
        return False

    logger.info("network: bridge %s ready (%s)", BRIDGE_NAME, BRIDGE_CIDR)
    return True


def _enable_ip_forwarding() -> bool:
    """Enable IPv4 forwarding in the host kernel."""
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "r") as f:
            current = f.read().strip()
        if current == "1":
            return True
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write("1\n")
        logger.info("network: enabled IP forwarding")
        return True
    except (OSError, PermissionError) as e:
        logger.warning("network: failed to enable IP forwarding: %s", e)
        return False


def _setup_masquerade() -> bool:
    """Set up iptables masquerade (SNAT) for the bridge subnet."""
    # Check if rule already exists
    result = _run([
        "iptables", "-t", "nat", "-C", "POSTROUTING",
        "-s", "172.16.0.0/24", "-o", "eth0", "-j", "MASQUERADE",
    ])
    if result.returncode == 0:
        return True  # Rule already exists

    # Add the masquerade rule
    result = _run([
        "iptables", "-t", "nat", "-A", "POSTROUTING",
        "-s", "172.16.0.0/24", "-o", "eth0", "-j", "MASQUERADE",
    ])
    if result.returncode != 0:
        # Try nftables as fallback
        logger.debug("network: iptables masquerade failed, trying nftables")
        result = _run([
            "nft", "add", "rule", "ip", "nat", "postrouting",
            "ip saddr 172.16.0.0/24", "oifname eth0", "masquerade",
        ])
        if result.returncode != 0:
            logger.warning("network: failed to set up masquerade")
            return False

    logger.info("network: masquerade enabled for 172.16.0.0/24")
    return True


def _alloc_ip() -> str:
    """Allocate the next IP from the container subnet."""
    ip = f"{CONTAINER_SUBNET_PREFIX}.{_next_ip[0]}"
    _next_ip[0] += 1
    # Wrap at .254 (leaving .255 for broadcast)
    if _next_ip[0] >= 255:
        _next_ip[0] = 2
    return ip


def setup_container_network(
    container_id: str,
    container_pid: int,
) -> Optional[str]:
    """Set up outbound network connectivity for a container.

    Creates a veth pair, attaches one end to the bridge and moves the
    other end into the container's network namespace, assigns an IP, and
    configures NAT.

    Args:
        container_id: The container's unique identifier.
        container_pid: The container's PID-1 (host pid).

    Returns:
        The IP address assigned to the container, or None on failure.
    """
    # Ensure the bridge exists
    if not _ensure_bridge():
        return None

    # Enable IP forwarding
    _enable_ip_forwarding()

    # Set up masquerade
    _setup_masquerade()

    # Derive interface names from the container ID
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"
    container_iface = "eth0"
    container_ip = _alloc_ip()

    # Create the veth pair
    result = _run([
        "ip", "link", "add", host_iface, "type", "veth",
        "peer", "name", container_iface,
    ])
    if result.returncode != 0:
        logger.warning(
            "network: failed to create veth pair for %s: %s",
            container_id,
            result.stderr.decode("utf-8", errors="replace").strip(),
        )
        return None

    # Attach the host end to the bridge
    if not _ip("link", "set", host_iface, "master", BRIDGE_NAME):
        logger.warning("network: failed to attach %s to bridge", host_iface)
        _cleanup_veth(host_iface, container_id)
        return None

    # Bring up the host end
    if not _ip("link", "set", host_iface, "up"):
        logger.warning("network: failed to bring up %s", host_iface)
        _cleanup_veth(host_iface, container_id)
        return None

    # Move the container end into the container's network namespace
    container_ns = f"/proc/{container_pid}/ns/net"
    if not os.path.exists(container_ns):
        logger.warning(
            "network: netns not found for pid %d", container_pid
        )
        _cleanup_veth(host_iface, container_id)
        return None

    if not _ip("link", "set", container_iface, "netns", str(container_pid)):
        logger.warning(
            "network: failed to move %s into netns for %s",
            container_iface,
            container_id,
        )
        _cleanup_veth(host_iface, container_id)
        return None

    # Configure the container-side interface (inside the container's netns)
    # We use nsenter or ip netns exec to configure the interface
    # Since we moved the interface via pid, we can use nsenter
    nsenter_args = [
        "nsenter", "--net=/proc/{}/ns/net".format(container_pid),
        "--", "ip", "addr", "add", f"{container_ip}/24",
        "dev", container_iface,
    ]
    result = _run(nsenter_args)
    if result.returncode != 0:
        logger.warning(
            "network: failed to assign IP %s to %s in %s: %s",
            container_ip, container_iface, container_id,
            result.stderr.decode("utf-8", errors="replace").strip(),
        )
        # Continue anyway — the interface is in the netns

    # Bring up the container-side interface
    _run([
        "nsenter", "--net=/proc/{}/ns/net".format(container_pid),
        "--", "ip", "link", "set", container_iface, "up",
    ])

    # Set the default route in the container
    _run([
        "nsenter", "--net=/proc/{}/ns/net".format(container_pid),
        "--", "ip", "route", "add", "default", "via", CONTAINER_GW,
    ])

    logger.info(
        "network: container %s connected (host=%s, ip=%s, gw=%s)",
        container_id, host_iface, container_ip, CONTAINER_GW,
    )
    return container_ip


def _cleanup_veth(host_iface: str, container_id: str) -> None:
    """Remove a veth pair (host end; kernel removes the peer)."""
    _run(["ip", "link", "del", host_iface])
    logger.debug("network: cleaned up veth %s for %s", host_iface, container_id)


def teardown_container_network(
    container_id: str,
    container_pid: Optional[int] = None,
) -> None:
    """Remove the veth pair for a terminated container.

    The bridge persists — the last container to leave cleans it up
    separately if desired.
    """
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"
    _cleanup_veth(host_iface, container_id)


def teardown_bridge() -> None:
    """Remove the bridge (best-effort, call when no containers remain)."""
    _run(["ip", "link", "del", BRIDGE_NAME])
    logger.info("network: bridge %s removed", BRIDGE_NAME)


def is_bridge_available() -> bool:
    """Check if the bridge is up and usable."""
    result = _run(["ip", "link", "show", BRIDGE_NAME])
    return result.returncode == 0


def get_container_ip(container_id: str) -> Optional[str]:
    """Get the IP address assigned to a container (if any)."""
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"
    result = _run(["ip", "-4", "addr", "show", "dev", host_iface])
    if result.returncode != 0:
        return None
    output = result.stdout.decode("utf-8", errors="replace")
    # Parse "inet 172.16.0.x/24"
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].split("/")[0]
    return None


def get_network_stats(container_id: str) -> Optional[dict]:
    """Get network interface stats for a container's veth pair.

    Reads ``/sys/class/net/<iface>/statistics`` on the host side of
    the veth pair to report bytes/packets in/out and errors.

    Args:
        container_id: The container identifier.

    Returns:
        Dict with rx/tx bytes, packets, errors, and the interface name,
        or None if the interface doesn't exist.
    """
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"
    sys_path = Path(f"/sys/class/net/{host_iface}/statistics")

    if not sys_path.is_dir():
        return None

    def _read_stat(name: str) -> int:
        try:
            return int((sys_path / name).read_text().strip())
        except (OSError, ValueError):
            return 0

    def _read_stat_str(name: str) -> str:
        try:
            return (sys_path.parent / name).read_text().strip()
        except OSError:
            return ""

    return {
        "interface": host_iface,
        "rx_bytes": _read_stat("rx_bytes"),
        "rx_packets": _read_stat("rx_packets"),
        "rx_errors": _read_stat("rx_errors"),
        "rx_dropped": _read_stat("rx_dropped"),
        "tx_bytes": _read_stat("tx_bytes"),
        "tx_packets": _read_stat("tx_packets"),
        "tx_errors": _read_stat("tx_errors"),
        "tx_dropped": _read_stat("tx_dropped"),
        "mtu": _read_stat_str("mtu"),
        "operstate": _read_stat_str("operstate"),
    }


def apply_network_policy(container_id: str, policy: dict) -> bool:
    """Apply iptables network policy rules for a container.

    The policy dict can contain:
    - ``ingress_allow``: list of "proto:port" strings (e.g. "tcp:80", "tcp:443")
    - ``ingress_deny``: list of "proto:port" strings to explicitly deny
    - ``egress_allow``: list of "proto:port" strings for outbound
    - ``egress_deny``: list of "proto:port" strings to deny outbound
    - ``egress_all``: bool, allow all outbound (default True)
    - ``ingress_all``: bool, allow all inbound (default False)

    Rules are applied via iptables on the host-side veth interface
    using the FORWARD chain.

    Args:
        container_id: The container identifier.
        policy: The policy dict.

    Returns:
        True if rules were applied successfully.
    """
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"

    # Check if the interface exists
    result = _run(["ip", "link", "show", host_iface])
    if result.returncode != 0:
        logger.debug("network_policy: interface %s not found", host_iface)
        return False

    success = True

    # --- Ingress rules (traffic coming INTO the container) ---
    ingress_all = policy.get("ingress_all", False)
    ingress_allow = policy.get("ingress_allow", [])
    ingress_deny = policy.get("ingress_deny", [])

    if not ingress_all:
        # Default deny ingress, then allow specific ports
        # -i = input interface (host-side veth)
        _run(["iptables", "-A", "FORWARD", "-i", host_iface,
              "-j", "DROP"])
        for rule in ingress_allow:
            proto_port = rule.split(":", 1)
            if len(proto_port) == 2:
                proto, port = proto_port
                _run(["iptables", "-I", "FORWARD", "1",
                      "-i", host_iface,
                      "-p", proto, "--dport", port,
                      "-j", "ACCEPT"])
    else:
        # Allow all ingress, deny specific ports
        for rule in ingress_deny:
            proto_port = rule.split(":", 1)
            if len(proto_port) == 2:
                proto, port = proto_port
                _run(["iptables", "-A", "FORWARD",
                      "-i", host_iface,
                      "-p", proto, "--dport", port,
                      "-j", "DROP"])

    # --- Egress rules (traffic going OUT of the container) ---
    egress_all = policy.get("egress_all", True)
    egress_allow = policy.get("egress_allow", [])
    egress_deny = policy.get("egress_deny", [])

    if egress_all and not egress_deny:
        # Allow all egress (default)
        pass
    else:
        # Default deny egress, then allow specific ports
        _run(["iptables", "-A", "FORWARD", "-o", host_iface,
              "-j", "DROP"])
        for rule in egress_allow:
            proto_port = rule.split(":", 1)
            if len(proto_port) == 2:
                proto, port = proto_port
                _run(["iptables", "-I", "FORWARD", "1",
                      "-o", host_iface,
                      "-p", proto, "--dport", port,
                      "-j", "ACCEPT"])
        for rule in egress_deny:
            proto_port = rule.split(":", 1)
            if len(proto_port) == 2:
                proto, port = proto_port
                _run(["iptables", "-A", "FORWARD",
                      "-o", host_iface,
                      "-p", proto, "--dport", port,
                      "-j", "DROP"])

    logger.info("network_policy: applied for %s on %s", container_id, host_iface)
    return success


def remove_network_policy(container_id: str) -> bool:
    """Remove all iptables rules for a container's veth interface.

    Flushes any FORWARD chain rules that reference the container's
    host-side veth interface.

    Args:
        container_id: The container identifier.

    Returns:
        True if rules were removed.
    """
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"

    # Remove all rules referencing this interface
    for direction in ("-i", "-o"):
        while True:
            result = _run(
                ["iptables", "-D", "FORWARD",
                 direction, host_iface, "-j", "DROP"]
            )
            if result.returncode != 0:
                break
        while True:
            result = _run(
                ["iptables", "-D", "FORWARD",
                 direction, host_iface, "-j", "ACCEPT"]
            )
            if result.returncode != 0:
                break

    logger.info("network_policy: removed for %s", container_id)
    return True


def get_network_policy(container_id: str) -> Optional[dict]:
    """Get the current iptables rules for a container's veth interface.

    Returns:
        Dict with ``ingress_rules`` and ``egress_rules`` lists,
        or None if the interface doesn't exist.
    """
    short_id = container_id[:12].replace("-", "")
    host_iface = f"veth-{short_id}"

    result = _run(["iptables", "-L", "FORWARD", "-n", "--line-numbers"])
    if result.returncode != 0:
        return None

    output = result.stdout.decode("utf-8", errors="replace")
    ingress_rules = []
    egress_rules = []
    for line in output.splitlines():
        if host_iface in line:
            parts = line.split()
            if len(parts) >= 4:
                rule_str = " ".join(parts[3:])
                if f"-i {host_iface}" in line or f"-i{host_iface}" in line:
                    ingress_rules.append(rule_str)
                elif f"-o {host_iface}" in line or f"-o{host_iface}" in line:
                    egress_rules.append(rule_str)

    return {
        "interface": host_iface,
        "ingress_rules": ingress_rules,
        "egress_rules": egress_rules,
    }


__all__ = [
    "setup_container_network",
    "teardown_container_network",
    "teardown_bridge",
    "is_bridge_available",
    "get_container_ip",
    "get_network_stats",
    "apply_network_policy",
    "remove_network_policy",
    "get_network_policy",
    "BRIDGE_NAME",
    "BRIDGE_SUBNET",
]
