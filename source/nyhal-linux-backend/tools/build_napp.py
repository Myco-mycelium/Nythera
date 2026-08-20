#!/usr/bin/env python3
"""Build a Nyrqis application package (.napp).

A Nyrqis application is a self-contained package that can be loaded
and executed by the NyRuntime inside a container.

Package format:
    [magic: 4 bytes]        "NYAP"
    [version: 1 byte]       Package format version (1)
    [manifest_len: 4 bytes] JSON manifest length (little-endian u32)
    [manifest: N bytes]     JSON manifest (UTF-8)
    [code_len: 4 bytes]     Code segment length (little-endian u32)
    [data_len: 4 bytes]     Data segment length (little-endian u32)
    [code: code_len bytes]  Code segment (opcodes)
    [data: data_len bytes]  Data segment (initial state)

Manifest format (JSON):
{
    "name": "hello-world",
    "version": "1.0.0",
    "description": "A simple hello world application",
    "author": "Nyrqis Team",
    "entry_point": 0,
    "required_capabilities": ["ipc_send", "ipc_receive"],
    "provided_services": [],
    "dependencies": [],
    "permissions": {
        "filesystem_read": true,
        "filesystem_write": false,
        "network": false
    }
}

Opcodes:
    0x00 = HALT (exit with data[0] as code)
    0x01 = NOP  (no operation, advance PC)
    0x02 = IPC_CALL (call a service: data[pc+1] = service name index,
                     data[pc+2] = operation name index,
                     data[pc+3] = payload index)
    0x03 = IPC_SEND (fire-and-forget: same args as IPC_CALL)
    0x04 = FS_READ (read file: data[pc+1] = path index, store at data[pc+2])
    0x05 = FS_WRITE (write file: data[pc+1] = path index, data[pc+2] = content index)
    0x06 = LOG (log message: data[pc+1] = message index)
    0x07 = SET_STATE (set state: data[pc+1] = key index, data[pc+2] = value index)
    0x08 = GET_STATE (get state: data[pc+1] = key index, store result)
    0x09 = YIELD (yield to runtime, advance PC)

Usage:
    python3 tools/build_napp.py --name hello --version 1.0.0 \\
        --manifest '{"name":"hello","version":"1.0.0",...}' \\
        --code 06,00,00 --data 72,101,108,108,111,0 \\
        --output hello.napp

    python3 tools/build_napp.py --name hello --version 1.0.0 \\
        --code 06,00,00 --data "Hello from Nyrqis!" \\
        --output hello.napp
"""

import argparse
import json
import struct
import sys
from pathlib import Path


MAGIC = b"NYAP"
VERSION = 1

# Opcode constants
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


def build_manifest(
    name: str,
    version: str = "1.0.0",
    description: str = "",
    author: str = "",
    entry_point: int = 0,
    required_capabilities: list = None,
    permissions: dict = None,
) -> str:
    """Build a JSON manifest."""
    manifest = {
        "name": name,
        "version": version,
        "description": description,
        "author": author,
        "entry_point": entry_point,
        "required_capabilities": required_capabilities or [],
        "provided_services": [],
        "dependencies": [],
        "permissions": permissions or {
            "filesystem_read": True,
            "filesystem_write": False,
            "network": False,
        },
    }
    return json.dumps(manifest, indent=2)


def build_napp(manifest: str, code: bytes, data: bytes) -> bytes:
    """Build a .napp binary."""
    manifest_bytes = manifest.encode("utf-8")
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
        raise ValueError(f"invalid magic: {magic!r}")

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
    }


def main():
    parser = argparse.ArgumentParser(
        description="Build a Nyrqis application package"
    )
    parser.add_argument("--output", "-o", required=True, help="Output .napp file")
    parser.add_argument("--name", required=True, help="Application name")
    parser.add_argument("--version", default="1.0.0", help="Application version")
    parser.add_argument("--description", default="", help="Application description")
    parser.add_argument("--author", default="", help="Application author")
    parser.add_argument("--entry", type=int, default=0, help="Entry point offset")
    parser.add_argument(
        "--caps",
        default="",
        help="Required capabilities (comma-separated)",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="Full JSON manifest (overrides other manifest fields)",
    )
    parser.add_argument(
        "--code",
        default="00",
        help="Code segment as comma-separated hex bytes (default: 0x00 = HALT)",
    )
    parser.add_argument(
        "--data",
        default="0",
        help="Data segment as comma-separated decimal bytes or a string",
    )
    args = parser.parse_args()

    # Build manifest
    if args.manifest:
        manifest = args.manifest
    else:
        caps = [c.strip() for c in args.caps.split(",") if c.strip()]
        manifest = build_manifest(
            name=args.name,
            version=args.version,
            description=args.description,
            author=args.author,
            entry_point=args.entry,
            required_capabilities=caps,
        )

    # Parse code
    code = bytes(int(x.strip(), 16) for x in args.code.split(",") if x.strip())

    # Parse data (support both hex bytes and string)
    data_str = args.data.strip()
    if data_str.startswith('"') and data_str.endswith('"'):
        # String data
        data = data_str[1:-1].encode("utf-8") + b"\x00"
    elif "," in data_str and all(
        x.strip().isdigit() for x in data_str.split(",") if x.strip()
    ):
        # Decimal bytes
        data = bytes(int(x.strip()) for x in data_str.split(",") if x.strip())
    else:
        # Try as integer
        try:
            data = bytes([int(data_str)])
        except ValueError:
            data = data_str.encode("utf-8") + b"\x00"

    napp = build_napp(manifest, code, data)

    Path(args.output).write_bytes(napp)

    # Parse and display
    parsed = parse_napp(napp)
    print(f"Built {args.output}: {len(napp)} bytes")
    print(f"  Name: {parsed['manifest']['name']}")
    print(f"  Version: {parsed['manifest']['version']}")
    print(f"  Code: {len(parsed['code'])} bytes")
    print(f"  Data: {len(parsed['data'])} bytes")
    print(f"  Capabilities: {parsed['manifest'].get('required_capabilities', [])}")


if __name__ == "__main__":
    main()
