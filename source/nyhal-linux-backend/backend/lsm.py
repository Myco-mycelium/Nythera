#!/usr/bin/env python3
"""
LSM — AppArmor / SELinux Policy Generation for the Nyrqis Linux Backend

Implements the second data-plane enforcement mechanism referenced in
NPS-017 §4.2 and IMPLEMENTATION_STATUS.md §2 outstanding work:

    LSM policy generation (AppArmor/SELinux) as a second data-plane
    mechanism

While seccomp-BPF operates at the *syscall* level (blocking whole
syscalls or flag-gated calls), LSMs operate at the *object* level —
denying access to specific files, paths, capabilities, network
resources, and device nodes even when the syscall itself is allowed.

Design:

- ``LSMPolicy`` — a declarative policy that captures what a container
  may access (paths, network ranges, capabilities, device nodes).
- ``AppArmorProfile`` — renders an AppArmor profile from an ``LSMPolicy``.
- ``SEPolicy`` — renders an SELinux policy module from an ``LSMPolicy``.
- ``build_lsm_policy`` — derives the ``LSMPolicy`` from a Nyrqis
  capability set (the same capabilities the seccomp policy consumes).
- ``generate_apparmor`` / ``generate_selinux`` — the two entry points
  that produce string policies ready for installation.

Trust model (same as seccomp): the backend compiles the policy from
the container's granted capability set; the launcher applies it inside
the container's execution context. The policy is a second enforcement
layer — even if seccomp allows a syscall, the LSM denies the object
access.

References:
- NPS-017 §4.2: Capability Enforcement
- NPS-011: Capability Registry
- FIND-BACKEND-002: the threat-model finding this layer reinforces
"""

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .capability import Capability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capability → path / network / device mappings
# ---------------------------------------------------------------------------

# AppArmor-style path permissions derived from Nyrqis capabilities.
# Each capability maps to a list of (path_pattern, permission_set) tuples.
_CAP_PATH_RULES: Dict[str, List[tuple]] = {
    Capability.CAP_FILESYSTEM_READ: [
        ("/usr/**", "r"),
        ("/bin/**", "r"),
        ("/lib/**", "r"),
        ("/lib64/**", "r"),
        ("/etc/**", "r"),
        ("/proc/**", "r"),
        ("/sys/**", "r"),
        ("/dev/null", "rw"),
        ("/dev/zero", "r"),
        ("/dev/urandom", "r"),
        ("/dev/random", "r"),
        ("/dev/full", "rw"),
    ],
    Capability.CAP_FILESYSTEM_WRITE: [
        ("/tmp/**", "rw"),
        ("/var/tmp/**", "rw"),
        ("/home/**", "rw"),
        ("/app/**", "rw"),
        ("/data/**", "rw"),
        ("/work/**", "rw"),
    ],
    Capability.CAP_DEVICE_ACCESS: [
        ("/dev/**", "rw"),
    ],
    Capability.CAP_GRAPHICS_RENDER: [
        ("/dev/dri/**", "rw"),
        ("/dev/nvidia**", "rw"),
        ("/sys/class/drm/**", "r"),
    ],
    Capability.CAP_AUDIO_PLAYBACK: [
        ("/dev/snd/**", "rw"),
        ("/dev/audio**", "rw"),
        ("/proc/asound/**", "r"),
    ],
    Capability.CAP_AUDIO_RECORD: [
        ("/dev/snd/**", "rw"),
        ("/dev/audio**", "rw"),
        ("/proc/asound/**", "r"),
    ],
    Capability.CAP_INPUT_DEVICE: [
        ("/dev/input/**", "rw"),
        ("/dev/event**", "rw"),
    ],
}

# AppArmor-style network rules derived from capabilities.
_CAP_NETWORK_RULES: Dict[str, List[str]] = {
    Capability.CAP_NETWORK_SOCKET: [
        "network inet stream",   # TCP
        "network inet dgram",    # UDP
        "network inet6 stream",
        "network inet6 dgram",
        "network unix stream",
        "network unix dgram",
    ],
    Capability.CAP_NETWORK_BIND: [
        "network inet stream",
        "network inet dgram",
        "network inet6 stream",
        "network inet6 dgram",
    ],
    Capability.CAP_NEAR_FIELD: [
        "network netlink nfqueue",
    ],
    Capability.CAP_CLOUD_SYNC: [
        "network inet stream",
        "network inet6 stream",
    ],
    Capability.CAP_TELEPHONY: [
        "network inet stream",
        "network inet dgram",
    ],
}

# Linux capabilities that AppArmor / SELinux can mediate.
_CAP_LINUX_CAPS: Dict[str, List[str]] = {
    Capability.CAP_PROCESS_SPAWN: ["cap_setuid", "cap_setgid"],
    Capability.CAP_FILESYSTEM_WRITE: ["cap_dac_override"],
    Capability.CAP_DEVICE_ACCESS: ["cap_sys_admin"],
    Capability.CAP_SYSTEM_TIME: ["cap_sys_time"],
    Capability.CAP_NETWORK_SOCKET: ["cap_net_admin"],
    Capability.CAP_GRAPHICS_RENDER: ["cap_sys_admin"],
}

# SELinux-type mappings: each Nyrqis capability maps to a set of
# SELinux object classes and permissions.
_CAP_SELINUX_RULES: Dict[str, List[Dict]] = {
    Capability.CAP_FILESYSTEM_READ: [
        {"class": "file", "perms": ["read", "open", "getattr"]},
        {"class": "dir", "perms": ["search", "read", "getattr", "open"]},
    ],
    Capability.CAP_FILESYSTEM_WRITE: [
        {"class": "file", "perms": [
            "read", "write", "open", "create", "append",
            "setattr", "unlink", "rename",
        ]},
        {"class": "dir", "perms": [
            "search", "read", "write", "add_name", "remove_name",
            "rename", "getattr", "setattr", "open",
        ]},
        {"class": "file", "perms": ["mounton"]},
    ],
    Capability.CAP_NETWORK_SOCKET: [
        {"class": "tcp_socket", "perms": [
            "create", "bind", "connect", "listen", "accept",
            "getopt", "setopt", "read", "write", "shutdown",
        ]},
        {"class": "udp_socket", "perms": [
            "create", "bind", "connect", "getopt", "setopt",
            "read", "write",
        ]},
        {"class": "unix_stream_socket", "perms": [
            "create", "bind", "connectto", "listen", "accept",
            "read", "write", "getattr",
        ]},
    ],
    Capability.CAP_NETWORK_BIND: [
        {"class": "tcp_socket", "perms": ["create", "bind", "listen"]},
        {"class": "udp_socket", "perms": ["create", "bind"]},
    ],
    Capability.CAP_PROCESS_SPAWN: [
        {"class": "process", "perms": [
            "fork", "transition", "sigchld", "signal",
        ]},
    ],
    Capability.CAP_DEVICE_ACCESS: [
        {"class": "chr_file", "perms": [
            "read", "write", "open", "getattr",
        ]},
        {"class": "blk_file", "perms": [
            "read", "write", "open", "getattr",
        ]},
    ],
    Capability.CAP_GRAPHICS_RENDER: [
        {"class": "chr_file", "perms": [
            "read", "write", "open", "ioctl", "mmap",
        ]},
    ],
    Capability.CAP_AUDIO_PLAYBACK: [
        {"class": "chr_file", "perms": [
            "read", "write", "open", "ioctl",
        ]},
    ],
    Capability.CAP_AUDIO_RECORD: [
        {"class": "chr_file", "perms": [
            "read", "write", "open", "ioctl",
        ]},
    ],
    Capability.CAP_INPUT_DEVICE: [
        {"class": "chr_file", "perms": [
            "read", "write", "open", "ioctl",
        ]},
    ],
    Capability.CAP_SYSTEM_TIME: [
        {"class": "system", "perms": ["settime"]},
    ],
    Capability.CAP_SYSTEM_INFO: [
        {"class": "system", "perms": ["module_request"]},
        {"class": "file", "perms": ["read", "open", "getattr"]},
    ],
    Capability.CAP_MEMORY_ALLOCATE: [
        {"class": "system", "perms": ["module_request"]},
    ],
}


# ---------------------------------------------------------------------------
# Declarative LSM policy
# ---------------------------------------------------------------------------

@dataclass
class LSMPathRule:
    """A single path-based access rule."""
    path: str          # glob pattern (AppArmor) or literal prefix (SELinux)
    perms: str         # AppArmor: "r", "w", "rw", "rwx", etc.
                       # SELinux: unused (SELinux uses class/perm maps)


@dataclass
class LSMNetworkRule:
    """A single network access rule."""
    family: str        # "inet", "inet6", "unix", "netlink"
    socket_type: str   # "stream", "dgram", etc.


@dataclass
class LSMDeviceRule:
    """A device node access rule."""
    path: str          # "/dev/dri/**", "/dev/input/**", etc.
    perms: str         # "r", "rw", etc.


@dataclass
class LSMPolicy:
    """Declarative LSM policy for a container.

    This is the intermediate representation that both AppArmor and
    SELinux backends consume.  Built from a Nyrqis capability set
    via ``build_lsm_policy``.
    """
    container_id: str
    capabilities: Set[Capability] = field(default_factory=set)
    path_rules: List[LSMPathRule] = field(default_factory=list)
    network_rules: List[LSMNetworkRule] = field(default_factory=list)
    device_rules: List[LSMDeviceRule] = field(default_factory=list)
    linux_capabilities: Set[str] = field(default_factory=set)
    # Deny rules (paths that are always blocked regardless of capabilities)
    deny_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Serialize for JSON persistence."""
        return {
            "container_id": self.container_id,
            "capabilities": sorted(c.value for c in self.capabilities),
            "path_rules": [
                {"path": r.path, "perms": r.perms}
                for r in self.path_rules
            ],
            "network_rules": [
                {"family": r.family, "socket_type": r.socket_type}
                for r in self.network_rules
            ],
            "device_rules": [
                {"path": r.path, "perms": r.perms}
                for r in self.device_rules
            ],
            "linux_capabilities": sorted(self.linux_capabilities),
            "deny_paths": sorted(self.deny_paths),
        }


def build_lsm_policy(
    container_id: str,
    capabilities: Set[Capability],
) -> LSMPolicy:
    """Derive an ``LSMPolicy`` from a Nyrqis capability set.

    This is the single entry point that maps Nyrqis capabilities (NPS-011)
    to LSM-accessible path, network, and device rules.  Both the AppArmor
    and SELinux generators consume the resulting policy.

    Always-denied paths (security hardening):
    - ``/proc/sysrq-trigger`` — system request key
    - ``/proc/sys/kernel/core_pattern`` — core dump handler
    - ``/proc/sys/kernel/modules_disabled`` — module unload
    - ``/sys/firmware/**`` — firmware interface
    """
    policy = LSMPolicy(
        container_id=container_id,
        capabilities=capabilities,
    )

    # Always deny dangerous paths regardless of capabilities
    policy.deny_paths = [
        "/proc/sysrq-trigger",
        "/proc/sys/kernel/core_pattern",
        "/proc/sys/kernel/modules_disabled",
        "/sys/firmware/**",
        "/proc/sys/kernel/hostname",
        "/proc/sys/net/ipv4/ip_forward",
        "/proc/sys/vm/overcommit_memory",
    ]

    # Build path rules from capabilities
    seen_paths: Set[str] = set()
    for cap in capabilities:
        rules = _CAP_PATH_RULES.get(cap, [])
        for path, perms in rules:
            if path not in seen_paths:
                policy.path_rules.append(LSMPathRule(path=path, perms=perms))
                seen_paths.add(path)

    # Build network rules from capabilities
    seen_net: Set[str] = set()
    for cap in capabilities:
        net_rules = _CAP_NETWORK_RULES.get(cap, [])
        for rule_str in net_rules:
            if rule_str not in seen_net:
                parts = rule_str.split()
                if len(parts) == 3 and parts[0] == "network":
                    family = parts[1]
                    sock_type = parts[2]
                    policy.network_rules.append(
                        LSMNetworkRule(family=family, socket_type=sock_type)
                    )
                    seen_net.add(rule_str)

    # Build device rules from capabilities
    seen_dev: Set[str] = set()
    for cap in capabilities:
        path_rules = _CAP_PATH_RULES.get(cap, [])
        for path, perms in path_rules:
            if path.startswith("/dev/") and path not in seen_dev:
                policy.device_rules.append(
                    LSMDeviceRule(path=path, perms=perms)
                )
                seen_dev.add(path)

    # Build Linux capability set
    for cap in capabilities:
        linux_caps = _CAP_LINUX_CAPS.get(cap, [])
        policy.linux_capabilities.update(linux_caps)

    return policy


# ---------------------------------------------------------------------------
# AppArmor profile generator
# ---------------------------------------------------------------------------

class AppArmorProfile:
    """Generates an AppArmor profile from an ``LSMPolicy``.

    The profile is a text file that can be loaded into the kernel's
    AppArmor LSM via ``aa-enforce`` or written to
    ``/etc/apparmor.d/nyrqis.<container-id>``.

    Output format follows the AppArmor 3.x profile syntax:
    ``#include <tunables/global>``, capability rules, path rules,
    network rules, and deny rules.
    """

    HEADER_TEMPLATE = """\
#include <tunables/global>

profile nyrqis.{container_id} flags=(attach_disconnected,mediate_deleted) {{
    # Nyrqis LSM policy for container {container_id}
    # Generated by nyrqis/backend/lsm.py — do not edit manually.
    # Capabilities: {capabilities}

    # --- Always-denied paths (security hardening) ---
{deny_rules}

    # --- Linux capabilities ---
{cap_rules}

    # --- Path access rules ---
{path_rules}

    # --- Network rules ---
{net_rules}

    # --- Device rules ---
{dev_rules}

    # Deny all writes to /proc/sys/** and /sys/** by default
    deny /proc/sys/** w,
    deny /sys/** w,
}}
"""

    def __init__(self, policy: LSMPolicy) -> None:
        self.policy = policy

    def render(self) -> str:
        """Render the complete AppArmor profile text."""
        p = self.policy

        # Deny rules
        deny_lines = []
        for path in sorted(p.deny_paths):
            deny_lines.append(f"    deny {path} rwxl,")
        deny_rules = "\n".join(deny_lines) if deny_lines else "    # (none)"

        # Capability rules
        cap_lines = []
        for cap in sorted(p.linux_capabilities):
            cap_lines.append(f"    capability {cap},")
        cap_rules = "\n".join(cap_lines) if cap_lines else "    # (none)"

        # Path rules — group by directory prefix for readability
        path_lines = []
        for rule in sorted(p.path_rules, key=lambda r: r.path):
            # Only include non-device paths (devices handled separately)
            if not rule.path.startswith("/dev/"):
                path_lines.append(f"    {rule.path} {rule.perms},")
        path_rules = "\n".join(path_lines) if path_lines else "    # (none)"

        # Network rules
        net_lines = []
        for rule in sorted(
            p.network_rules, key=lambda r: (r.family, r.socket_type)
        ):
            net_lines.append(
                f"    network {rule.family} {rule.socket_type},"
            )
        net_rules = "\n".join(net_lines) if net_lines else "    # (none)"

        # Device rules
        dev_lines = []
        for rule in sorted(p.device_rules, key=lambda r: r.path):
            dev_lines.append(f"    {rule.path} {rule.perms},")
        dev_rules = "\n".join(dev_lines) if dev_lines else "    # (none)"

        caps_str = ", ".join(
            sorted(c.value for c in p.capabilities)
        ) or "(none)"

        return self.HEADER_TEMPLATE.format(
            container_id=p.container_id,
            capabilities=caps_str,
            deny_rules=deny_rules,
            cap_rules=cap_rules,
            path_rules=path_rules,
            net_rules=net_rules,
            dev_rules=dev_rules,
        )

    def write(self, path: str) -> None:
        """Write the profile to disk."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.render())
        # AppArmor profiles should be world-readable
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
        logger.info("AppArmor profile written to %s", path)


# ---------------------------------------------------------------------------
# SELinux policy module generator
# ---------------------------------------------------------------------------

class SEPolicy:
    """Generates an SELinux policy module from an ``LSMPolicy``.

    The module is a ``.te`` (Type Enforcement) file that can be
    compiled with ``checkmodule`` and loaded with ``semodule_i``.

    Output follows the SELinux policy language:
    - A custom type for the container (``nyrqis_container_t``)
    - Allow rules derived from the capability set
    - File context entries for the container's data directories
    """

    def __init__(self, policy: LSMPolicy) -> None:
        self.policy = policy

    def render_type_enforcement(self) -> str:
        """Render the .te (Type Enforcement) policy module."""
        p = self.policy
        type_name = f"nyrqis_{p.container_id.replace('-', '_')}_t"
        domain = type_name

        lines = [
            f"policy_module(nyrqis_{p.container_id.replace('-', '_')}, 1.0)",
            "",
            f"# Nyrqis SELinux policy for container {p.container_id}",
            f"# Generated by nyrqis/backend/lsm.py — do not edit manually.",
            f"# Capabilities: {', '.join(c.value for c in sorted(p.capabilities, key=lambda c: c.value))}",
            "",
            "########################################",
            f"# {domain} domain definition",
            "########################################",
            "",
            f"type {domain}, domain;",
            f"type {domain}_exec_t, exec_type, file_type;",
            "",
            "# Allow transition into this domain",
            f"allow init_t {domain}:process transition;",
            f"allow {domain} init_t:process sigchld;",
            "",
        ]

        # Base domain permissions (file read, process basics)
        base_perms = [
            f"allow {domain} self:capability {cap};"
            for cap in sorted(p.linux_capabilities)
        ]
        if base_perms:
            lines.append("# Linux capabilities")
            lines.extend(base_perms)
            lines.append("")

        # Allow rules from SELinux capability mappings
        # Group permissions by class for valid SELinux syntax:
        #   allow domain target_class { perm1 perm2 ... };
        class_perms: Dict[str, Set[str]] = {}
        for cap in sorted(p.capabilities, key=lambda c: c.value):
            rules = _CAP_SELINUX_RULES.get(cap, [])
            for rule in rules:
                cls = rule["class"]
                if cls not in class_perms:
                    class_perms[cls] = set()
                class_perms[cls].update(rule["perms"])

        if class_perms:
            lines.append("# Capability-derived SELinux allow rules")
            for cls in sorted(class_perms):
                perms_str = " ".join(sorted(class_perms[cls]))
                lines.append(
                    f"allow {domain} {cls} {{ {perms_str} }};"
                )
            lines.append("")

        # File access rules for specific paths
        file_rules = []
        for rule in sorted(p.path_rules, key=lambda r: r.path):
            if rule.path.startswith("/dev/"):
                # Device nodes — map to chr_file / blk_file
                if "**" in rule.path:
                    continue  # Glob patterns need file_contexts
                file_rules.append(
                    f"allow {domain} {rule.path}:chr_file "
                    f"{{ read write open getattr ioctl }};"
                )

        if file_rules:
            lines.append("# Device access rules")
            lines.extend(sorted(set(file_rules)))
            lines.append("")

        # Deny rules for always-blocked paths
        deny_lines = []
        for path in sorted(p.deny_paths):
            if "**" in path:
                # For globs, deny the directory
                parent = path.split("/**")[0]
                deny_lines.append(
                    f"neverallow {domain} {parent}:dir "
                    f"{{ write add_name remove_name }};"
                )
            else:
                deny_lines.append(
                    f"neverallow {domain} {path}:file "
                    f"{{ write create append }};"
                )

        if deny_lines:
            lines.append("# Always-denied paths (security hardening)")
            lines.extend(deny_lines)
            lines.append("")

        # Network access rules
        net_rules = []
        for rule in sorted(
            p.network_rules, key=lambda r: (r.family, r.socket_type)
        ):
            if rule.family in ("inet", "inet6"):
                if rule.socket_type == "stream":
                    net_rules.append(
                        f"allow {domain} tcp_socket "
                        f"{{ create bind connect listen accept "
                        f"getopt setopt read write shutdown }};"
                    )
                elif rule.socket_type == "dgram":
                    net_rules.append(
                        f"allow {domain} udp_socket "
                        f"{{ create bind connect getopt setopt read write }};"
                    )
            elif rule.family == "unix":
                net_rules.append(
                    f"allow {domain} unix_stream_socket "
                    f"{{ create bind connectto listen accept read write }};"
                )

        if net_rules:
            lines.append("# Network access rules")
            lines.extend(sorted(set(net_rules)))
            lines.append("")

        # IPC rules (needed for the Nyrqis IPC transport)
        ipc_perms = []
        if Capability.CAP_IPC_SEND in p.capabilities:
            ipc_perms.append("write")
            ipc_perms.append("sendto")
        if Capability.CAP_IPC_RECEIVE in p.capabilities:
            ipc_perms.append("read")
            ipc_perms.append("recvfrom")

        if ipc_perms:
            lines.append("# IPC transport rules (Unix datagrams)")
            lines.append(
                f"allow {domain} unix_dgram_socket "
                f"{{ create bind sendto recvfrom read write getattr }};"
            )
            lines.append("")

        lines.append("# Never allow privileged operations")
        lines.append(
            f"neverallow {domain} self:capability "
            "{ sys_admin sys_rawio sys_module sys_nice sys_tty_config "
            "net_admin net_raw mknod };"
        )
        lines.append("")

        return "\n".join(lines)

    def render_file_contexts(self) -> str:
        """Render the .fc (File Contexts) policy module."""
        p = self.policy
        type_name = f"nyrqis_{p.container_id.replace('-', '_')}_t"

        lines = [
            f"# Nyrqis SELinux file contexts for container {p.container_id}",
            f"# Generated by nyrqis/backend/lsm.py",
            "",
        ]

        # Container data directories
        data_dirs = [
            "/tmp/nyrqis-%s" % p.container_id,
            "/var/lib/nyrqis/containers/%s" % p.container_id,
        ]
        for d in data_dirs:
            lines.append(f"{d}(/.*)?    gen_context(system_u:object_r:{type_name}_t,s0)")

        lines.append("")
        return "\n".join(lines)

    def render_spec(self) -> str:
        """Render the .sp (Interface specification) — minimal."""
        p = self.policy
        type_name = f"nyrqis_{p.container_id.replace('-', '_')}_t"
        domain_name = f"nyrqis_{p.container_id.replace('-', '_')}_domain"

        lines = [
            f"## Nyrqis container interface for {p.container_id}",
            f"## Generated by nyrqis/backend/lsm.py",
            "",
            f"interface(`{domain_name}',`",
            f"    gen_require(`",
            f"        type {type_name};",
            f"    ')",
            f"    type_transition init_t {type_name}_exec_t:process {type_name};",
            f"')",
            "",
        ]
        return "\n".join(lines)

    def write(self, directory: str) -> Dict[str, str]:
        """Write the policy module files to a directory.

        Returns a dict mapping file extension to written path.
        """
        os.makedirs(directory, exist_ok=True)
        paths = {}

        te_path = os.path.join(
            directory, f"nyrqis_{self.policy.container_id}.te"
        )
        with open(te_path, "w", encoding="utf-8") as f:
            f.write(self.render_type_enforcement())
        os.chmod(te_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
        paths[".te"] = te_path

        fc_path = os.path.join(
            directory, f"nyrqis_{self.policy.container_id}.fc"
        )
        with open(fc_path, "w", encoding="utf-8") as f:
            f.write(self.render_file_contexts())
        os.chmod(fc_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
        paths[".fc"] = fc_path

        sp_path = os.path.join(
            directory, f"nyrqis_{self.policy.container_id}.sp"
        )
        with open(sp_path, "w", encoding="utf-8") as f:
            f.write(self.render_spec())
        os.chmod(sp_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
        paths[".sp"] = sp_path

        logger.info(
            "SELinux policy module written to %s (%s)",
            directory,
            ", ".join(f"{k}={v}" for k, v in paths.items()),
        )
        return paths


# ---------------------------------------------------------------------------
# Public API — the two entry points
# ---------------------------------------------------------------------------

def generate_apparmor(
    container_id: str,
    capabilities: Set[Capability],
) -> str:
    """Generate an AppArmor profile string for a container.

    Args:
        container_id: The container's unique identifier.
        capabilities: The Nyrqis capability set to derive the policy from.

    Returns:
        The complete AppArmor profile text.
    """
    policy = build_lsm_policy(container_id, capabilities)
    profile = AppArmorProfile(policy)
    return profile.render()


def generate_selinux_te(
    container_id: str,
    capabilities: Set[Capability],
) -> str:
    """Generate an SELinux Type Enforcement module string for a container.

    Args:
        container_id: The container's unique identifier.
        capabilities: The Nyrqis capability set to derive the policy from.

    Returns:
        The complete .te (Type Enforcement) policy text.
    """
    policy = build_lsm_policy(container_id, capabilities)
    se = SEPolicy(policy)
    return se.render_type_enforcement()


def lsm_audit(policy: LSMPolicy) -> List[str]:
    """Audit an LSM policy for potential issues.

    Returns a list of warning strings.  Empty means the policy looks
    clean.
    """
    warnings = []

    # Warn if too many capabilities are granted
    if len(policy.capabilities) > 15:
        warnings.append(
            f"policy grants {len(policy.capabilities)} capabilities — "
            "consider narrowing the grant set"
        )

    # Warn if broad device access is granted
    if Capability.CAP_DEVICE_ACCESS in policy.capabilities:
        warnings.append(
            "CAP_DEVICE_ACCESS grants access to /dev/** — "
            "consider a narrower device allowlist"
        )

    # Warn if network is granted without explicit scope
    has_network = (
        Capability.CAP_NETWORK_SOCKET in policy.capabilities
        or Capability.CAP_NETWORK_BIND in policy.capabilities
    )
    if has_network and len(policy.network_rules) > 6:
        warnings.append(
            f"policy grants {len(policy.network_rules)} network families — "
            "consider restricting to needed families only"
        )

    # Warn if no deny paths are set (missing security hardening)
    if not policy.deny_paths:
        warnings.append(
            "policy has no deny_paths — security hardening paths "
            "(/proc/sysrq-trigger, /sys/firmware, etc.) are not blocked"
        )

    return warnings


__all__ = [
    "LSMPolicy",
    "LSMPathRule",
    "LSMNetworkRule",
    "LSMDeviceRule",
    "AppArmorProfile",
    "SEPolicy",
    "build_lsm_policy",
    "generate_apparmor",
    "generate_selinux_te",
    "lsm_audit",
]
