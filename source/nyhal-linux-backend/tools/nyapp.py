#!/usr/bin/env python3
"""nyapp — Nyrqis Application Packager and Runner.

Builds, inspects, and runs Nyrqis application packages (.napp).
Integrates with NyRuntime for execution and the desktop shell for launching.

Package format (v1):
    [magic: 4 bytes]        "NYAP"
    [version: 1 byte]       Package format version (1)
    [manifest_len: 4 bytes] JSON manifest length (u32 LE)
    [manifest: N bytes]     JSON manifest (UTF-8)
    [code_len: 4 bytes]     Code segment length (u32 LE)
    [data_len: 4 bytes]     Data segment length (u32 LE)
    [code: code_len bytes]  Code segment (opcodes)
    [data: data_len bytes]  Data segment (initial state)

Usage::

    # Build a .napp from source
    nyapp.py build --name hello --source hello.py --output hello.napp

    # Inspect a .napp package
    nyapp.py inspect hello.napp

    # Run a .napp package
    nyapp.py run hello.napp

    # List installed apps
    nyapp.py list

    # Install a .napp to the app directory
    nyapp.py install hello.napp

References:
    - NFS-001 §5: application model
    - ADR-0025: runtime consumption decision
    - NPS-008: package format
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure the backend root is on the path
_backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC = b"NYAP"
VERSION = 1

# Opcodes (must match rust/nyruntime/src/lib.rs)
OP_HALT = 0x00
OP_NOP = 0x01
OP_IPC_CALL = 0x02
OP_IPC_SEND = 0x03
OP_FS_READ = 0x04
OP_FS_WRITE = 0x05
OP_LOG = 0x06
OP_SET_STATE = 0x07
OP_GET_STATE = 0x08
OP_YIELD = 0x09

OPCODE_NAMES = {
    OP_HALT: "HALT",
    OP_NOP: "NOP",
    OP_IPC_CALL: "IPC_CALL",
    OP_IPC_SEND: "IPC_SEND",
    OP_FS_READ: "FS_READ",
    OP_FS_WRITE: "FS_WRITE",
    OP_LOG: "LOG",
    OP_SET_STATE: "SET_STATE",
    OP_GET_STATE: "GET_STATE",
    OP_YIELD: "YIELD",
}

# Default app directory
DEFAULT_APP_DIR = os.path.expanduser("~/.nyrqis/apps")


# ---------------------------------------------------------------------------
# .napp binary format
# ---------------------------------------------------------------------------

def build_napp(manifest: dict, code: bytes, data: bytes) -> bytes:
    """Build a .napp binary from manifest, code, and data segments."""
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    header = struct.pack(
        "<4sBIII",
        MAGIC,
        VERSION,
        len(manifest_bytes),
        len(code),
        len(data),
    )
    return header + manifest_bytes + code + data


def parse_napp(data: bytes) -> dict:
    """Parse a .napp binary and return its components."""
    if len(data) < 17:
        raise ValueError("napp too small")

    magic = data[0:4]
    if magic != MAGIC:
        raise ValueError(f"invalid magic: {magic!r} (expected {MAGIC!r})")

    version = data[4]
    manifest_len = struct.unpack_from("<I", data, 5)[0]
    code_len = struct.unpack_from("<I", data, 9)[0]
    data_len = struct.unpack_from("<I", data, 13)[0]

    offset = 17
    manifest = json.loads(data[offset : offset + manifest_len])
    offset += manifest_len
    code = data[offset : offset + code_len]
    offset += code_len
    segment_data = data[offset : offset + data_len]

    return {
        "version": version,
        "manifest": manifest,
        "code": code,
        "data": segment_data,
        "total_size": len(data),
    }


def validate_napp(data: bytes) -> List[str]:
    """Validate a .napp binary and return a list of issues."""
    issues = []
    try:
        parsed = parse_napp(data)
    except Exception as e:
        return [f"parse error: {e}"]

    manifest = parsed["manifest"]

    # Required manifest fields
    for field in ("name", "version"):
        if field not in manifest:
            issues.append(f"manifest missing required field: {field}")

    # Code must not be empty
    if len(parsed["code"]) == 0:
        issues.append("code segment is empty")

    # Entry point must be within code bounds
    entry = manifest.get("entry_point", 0)
    if entry >= len(parsed["code"]):
        issues.append(f"entry_point {entry} exceeds code length {len(parsed['code'])}")

    # Version must be 1
    if parsed["version"] != VERSION:
        issues.append(f"unsupported version {parsed['version']} (expected {VERSION})")

    return issues


# ---------------------------------------------------------------------------
# Source compilation (Python → opcodes)
# ---------------------------------------------------------------------------

def compile_source(source: str, name: str) -> Tuple[bytes, bytes]:
    """Compile a Python source file to .napp opcodes.

    This is a minimal compiler that translates a subset of Python
    into the Nyrqis opcode format.  For full applications, the NyRuntime
    Rust crate handles execution directly.

    Supported patterns:
    - print("message") → LOG opcode
    - sys.exit(code) → HALT opcode
    - Simple assignments → SET_STATE opcodes

    Returns (code_bytes, data_bytes).
    """
    code = bytearray()
    data = bytearray()
    data_strings: List[str] = []

    def add_string(s: str) -> int:
        """Add a string to the data segment, return its index."""
        idx = len(data_strings)
        data_strings.append(s)
        return idx

    lines = source.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # print("message") → LOG
        if line.startswith("print(") and line.endswith(")"):
            msg = line[6:-1].strip()
            if msg.startswith('"') and msg.endswith('"'):
                msg = msg[1:-1]
            elif msg.startswith("'") and msg.endswith("'"):
                msg = msg[1:-1]
            idx = add_string(msg)
            code.extend([OP_LOG, idx, 0x00])

        # sys.exit(code) → HALT
        elif line.startswith("sys.exit(") or line.startswith("exit("):
            code.extend([OP_HALT, 0x00])

        # state[key] = value → SET_STATE
        elif "state[" in line and "=" in line:
            parts = line.split("=")
            if len(parts) == 2:
                key_part = parts[0].strip()
                val_part = parts[1].strip()
                key = key_part.split("[")[-1].strip().strip('"').strip("'")
                val = val_part.strip('"').strip("'")
                key_idx = add_string(key)
                val_idx = add_string(val)
                code.extend([OP_SET_STATE, key_idx, val_idx])

        else:
            # Unknown line → NOP
            code.extend([OP_NOP])

    # Final HALT
    code.extend([OP_HALT, 0x00])

    # Pack data strings
    data_bytes = bytearray()
    for s in data_strings:
        encoded = s.encode("utf-8") + b"\x00"
        data_bytes.extend(struct.pack("<I", len(encoded)))
        data_bytes.extend(encoded)

    return bytes(code), bytes(data_bytes)


# ---------------------------------------------------------------------------
# NyApp Packager
# ---------------------------------------------------------------------------

class NyAppPackager:
    """Packager for building, inspecting, and managing Nyrqis applications."""

    def __init__(self, app_dir: Optional[str] = None):
        self._app_dir = app_dir or DEFAULT_APP_DIR
        os.makedirs(self._app_dir, exist_ok=True)

    @property
    def app_dir(self) -> str:
        return self._app_dir

    def build(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        author: str = "",
        source: Optional[str] = None,
        code_bytes: Optional[bytes] = None,
        data_bytes: Optional[bytes] = None,
        capabilities: Optional[List[str]] = None,
        permissions: Optional[Dict[str, bool]] = None,
        output: Optional[str] = None,
    ) -> bytes:
        """Build a .napp package.

        Parameters
        ----------
        name : str
            Application name.
        version : str
            Semantic version.
        description : str
            App description.
        author : str
            Author name.
        source : str, optional
            Python source file to compile.
        code_bytes : bytes, optional
            Pre-compiled code segment.
        data_bytes : bytes, optional
            Pre-compiled data segment.
        capabilities : list, optional
            Required capabilities.
        permissions : dict, optional
            Permission flags.
        output : str, optional
            Output file path.

        Returns
        -------
        bytes
            The .napp binary.
        """
        # Build manifest
        manifest = {
            "name": name,
            "version": version,
            "description": description,
            "author": author,
            "id": f"{name}-{uuid.uuid4().hex[:8]}",
            "entry_point": 0,
            "required_capabilities": capabilities or [],
            "provided_services": [],
            "dependencies": [],
            "permissions": permissions or {
                "filesystem_read": True,
                "filesystem_write": False,
                "network": False,
            },
        }

        # Compile source if provided
        if source is not None:
            with open(source, "r") as f:
                src = f.read()
            code, data = compile_source(src, name)
        elif code_bytes is not None:
            code = code_bytes
            data = data_bytes or b""
        else:
            # Default: just HALT
            code = bytes([OP_HALT])
            data = b""

        napp = build_napp(manifest, code, data)

        if output:
            Path(output).write_bytes(napp)

        return napp

    def inspect(self, path: str) -> dict:
        """Inspect a .napp package and return its metadata."""
        data = Path(path).read_bytes()
        parsed = parse_napp(data)

        # Disassemble code
        disassembly = self._disassemble(parsed["code"])

        return {
            "path": path,
            "size": len(data),
            "version": parsed["version"],
            "manifest": parsed["manifest"],
            "code_size": len(parsed["code"]),
            "data_size": len(parsed["data"]),
            "disassembly": disassembly,
            "issues": validate_napp(data),
        }

    def _disassemble(self, code: bytes) -> List[dict]:
        """Disassemble code bytes into a list of instructions."""
        instructions = []
        pc = 0
        while pc < len(code):
            opcode = code[pc]
            name = OPCODE_NAMES.get(opcode, f"UNKNOWN(0x{opcode:02x})")

            if opcode == OP_HALT:
                instructions.append({"pc": pc, "op": name, "args": [code[pc + 1] if pc + 1 < len(code) else 0]})
                pc += 2
            elif opcode == OP_NOP:
                instructions.append({"pc": pc, "op": name, "args": []})
                pc += 1
            elif opcode in (OP_IPC_CALL, OP_IPC_SEND):
                args = list(code[pc + 1:pc + 4]) if pc + 3 < len(code) else []
                instructions.append({"pc": pc, "op": name, "args": args})
                pc += 4
            elif opcode in (OP_FS_READ, OP_FS_WRITE):
                args = list(code[pc + 1:pc + 3]) if pc + 2 < len(code) else []
                instructions.append({"pc": pc, "op": name, "args": args})
                pc += 3
            elif opcode == OP_LOG:
                args = list(code[pc + 1:pc + 3]) if pc + 2 < len(code) else []
                instructions.append({"pc": pc, "op": name, "args": args})
                pc += 3
            elif opcode in (OP_SET_STATE, OP_GET_STATE):
                args = list(code[pc + 1:pc + 3]) if pc + 2 < len(code) else []
                instructions.append({"pc": pc, "op": name, "args": args})
                pc += 3
            elif opcode == OP_YIELD:
                instructions.append({"pc": pc, "op": name, "args": []})
                pc += 1
            else:
                instructions.append({"pc": pc, "op": name, "args": []})
                pc += 1

        return instructions

    def run(self, path: str) -> dict:
        """Run a .napp package.

        Tries NyRuntime (Rust crate) first; falls back to the Python
        opcode interpreter.

        Returns
        -------
        dict
            Execution result: exit_code, logs, duration.
        """
        data = Path(path).read_bytes()
        parsed = parse_napp(data)

        # Try Rust NyRuntime first
        try:
            return self._run_rust(data, parsed)
        except (ImportError, RuntimeError):
            pass

        # Fallback: Python interpreter
        return self._run_python(parsed)

    def _run_rust(self, data: bytes, parsed: dict) -> dict:
        """Run using the Rust NyRuntime crate."""
        from backend.nyruntime import NyRuntime

        with NyRuntime() as rt:
            rt.init()
            rt.load_napp(data)
            t0 = time.time()
            exit_code = rt.execute()
            duration = time.time() - t0

            logs = []
            for entry in rt.log_entries():
                logs.append({
                    "level": entry.level,
                    "message": entry.message.decode("utf-8", errors="replace")
                    if isinstance(entry.message, bytes)
                    else str(entry.message),
                })

            return {
                "ok": True,
                "backend": "rust",
                "exit_code": exit_code,
                "logs": logs,
                "duration_ms": round(duration * 1000, 2),
                "manifest": parsed["manifest"],
            }

    def _run_python(self, parsed: dict) -> dict:
        """Run using the Python opcode interpreter (fallback)."""
        code = parsed["code"]
        data = parsed["data"]
        manifest = parsed["manifest"]

        # Parse data segment: each string is [u32 length][bytes]
        strings: List[str] = []
        offset = 0
        while offset < len(data):
            if offset + 4 > len(data):
                break
            slen = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            if offset + slen > len(data):
                break
            s = data[offset:offset + slen]
            # Strip null terminator
            if s.endswith(b"\x00"):
                s = s[:-1]
            strings.append(s.decode("utf-8", errors="replace"))
            offset += slen

        # Execute opcodes
        pc = manifest.get("entry_point", 0)
        state: Dict[str, str] = {}
        logs: List[dict] = []
        t0 = time.time()

        while pc < len(code):
            opcode = code[pc]

            if opcode == OP_HALT:
                exit_code = code[pc + 1] if pc + 1 < len(code) else 0
                break

            elif opcode == OP_NOP:
                pc += 1

            elif opcode == OP_LOG:
                idx = code[pc + 1] if pc + 1 < len(code) else 0
                msg = strings[idx] if idx < len(strings) else ""
                logs.append({"level": 0, "message": msg})
                pc += 3

            elif opcode == OP_SET_STATE:
                key_idx = code[pc + 1] if pc + 1 < len(code) else 0
                val_idx = code[pc + 2] if pc + 2 < len(code) else 0
                key = strings[key_idx] if key_idx < len(strings) else ""
                val = strings[val_idx] if val_idx < len(strings) else ""
                state[key] = val
                pc += 3

            elif opcode == OP_GET_STATE:
                key_idx = code[pc + 1] if pc + 1 < len(code) else 0
                key = strings[key_idx] if key_idx < len(strings) else ""
                _ = state.get(key, "")
                pc += 3

            elif opcode == OP_IPC_CALL:
                pc += 4

            elif opcode == OP_IPC_SEND:
                pc += 4

            elif opcode == OP_FS_READ:
                pc += 3

            elif opcode == OP_FS_WRITE:
                pc += 3

            elif opcode == OP_YIELD:
                pc += 1

            else:
                pc += 1
        else:
            exit_code = 0

        duration = time.time() - t0

        return {
            "ok": True,
            "backend": "python",
            "exit_code": exit_code,
            "logs": logs,
            "state": state,
            "duration_ms": round(duration * 1000, 2),
            "manifest": manifest,
        }

    def install(self, path: str) -> str:
        """Install a .napp to the app directory.

        Returns the installed path.
        """
        data = Path(path).read_bytes()
        parsed = parse_napp(data)
        manifest = parsed["manifest"]

        app_name = manifest.get("name", "unknown")
        app_version = manifest.get("version", "0.0.0")
        app_id = manifest.get("id", f"{app_name}-{uuid.uuid4().hex[:8]}")

        # Create app directory
        app_path = os.path.join(self._app_dir, app_id)
        os.makedirs(app_path, exist_ok=True)

        # Copy .napp file
        dest = os.path.join(app_path, f"{app_name}.napp")
        Path(dest).write_bytes(data)

        # Write manifest separately for easy inspection
        manifest_path = os.path.join(app_path, "manifest.json")
        Path(manifest_path).write_text(json.dumps(manifest, indent=2))

        return dest

    def list_installed(self) -> List[dict]:
        """List all installed .napp packages."""
        apps = []
        if not os.path.exists(self._app_dir):
            return apps

        for entry in os.listdir(self._app_dir):
            app_path = os.path.join(self._app_dir, entry)
            if not os.path.isdir(app_path):
                continue

            manifest_path = os.path.join(app_path, "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path) as f:
                    manifest = json.load(f)
                apps.append({
                    "id": entry,
                    "name": manifest.get("name", "unknown"),
                    "version": manifest.get("version", "0.0.0"),
                    "description": manifest.get("description", ""),
                    "path": app_path,
                })

        return sorted(apps, key=lambda a: a["name"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Nyrqis Application Packager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # build
    build_p = sub.add_parser("build", help="Build a .napp package")
    build_p.add_argument("--name", required=True, help="App name")
    build_p.add_argument("--version", default="1.0.0", help="Version")
    build_p.add_argument("--description", default="", help="Description")
    build_p.add_argument("--author", default="", help="Author")
    build_p.add_argument("--source", "-s", help="Python source file")
    build_p.add_argument("--caps", default="", help="Required capabilities (comma-separated)")
    build_p.add_argument("--output", "-o", required=True, help="Output .napp file")

    # inspect
    inspect_p = sub.add_parser("inspect", help="Inspect a .napp package")
    inspect_p.add_argument("file", help=".napp file to inspect")
    inspect_p.add_argument("--json", "-j", action="store_true", help="JSON output")

    # run
    run_p = sub.add_parser("run", help="Run a .napp package")
    run_p.add_argument("file", help=".napp file to run")
    run_p.add_argument("--json", "-j", action="store_true", help="JSON output")

    # install
    install_p = sub.add_parser("install", help="Install a .napp package")
    install_p.add_argument("file", help=".napp file to install")

    # list
    sub.add_parser("list", help="List installed packages")

    args = parser.parse_args()

    packager = NyAppPackager()

    if args.command == "build":
        caps = [c.strip() for c in args.caps.split(",") if c.strip()]
        napp = packager.build(
            name=args.name,
            version=args.version,
            description=args.description,
            author=args.author,
            source=args.source,
            capabilities=caps,
            output=args.output,
        )
        parsed = parse_napp(napp)
        print(f"Built {args.output}: {len(napp)} bytes")
        print(f"  Name: {parsed['manifest']['name']}")
        print(f"  Version: {parsed['manifest']['version']}")
        print(f"  Code: {len(parsed['code'])} bytes")
        print(f"  Data: {len(parsed['data'])} bytes")

    elif args.command == "inspect":
        result = packager.inspect(args.file)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            m = result["manifest"]
            print(f"Package: {result['path']}")
            print(f"  Size: {result['size']} bytes")
            print(f"  Name: {m.get('name', '?')}")
            print(f"  Version: {m.get('version', '?')}")
            print(f"  Author: {m.get('author', '?')}")
            print(f"  Description: {m.get('description', '?')}")
            print(f"  Code: {result['code_size']} bytes")
            print(f"  Data: {result['data_size']} bytes")
            print(f"  Capabilities: {m.get('required_capabilities', [])}")
            print(f"  Permissions: {m.get('permissions', {})}")
            if result["issues"]:
                print(f"  Issues: {result['issues']}")
            print(f"\nDisassembly:")
            for instr in result["disassembly"]:
                args_str = ", ".join(str(a) for a in instr["args"])
                print(f"  {instr['pc']:04x}: {instr['op']:<12s} {args_str}")

    elif args.command == "run":
        result = packager.run(args.file)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Backend: {result['backend']}")
            print(f"Exit code: {result['exit_code']}")
            print(f"Duration: {result['duration_ms']}ms")
            if result.get("logs"):
                print(f"Logs:")
                for log in result["logs"]:
                    print(f"  [{log['level']}] {log['message']}")
            if result.get("state"):
                print(f"State: {result['state']}")

    elif args.command == "install":
        dest = packager.install(args.file)
        print(f"Installed to: {dest}")

    elif args.command == "list":
        apps = packager.list_installed()
        if not apps:
            print("No packages installed.")
        else:
            for app in apps:
                print(f"  {app['name']} v{app['version']} ({app['id']})")
                if app["description"]:
                    print(f"    {app['description']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
