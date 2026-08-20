#!/usr/bin/env python3
"""
status_client.py — First real Nyrqis application that calls the daemon
via IPC.

Demonstrates the full Nyrqis stack:
  .napp binary → NyRuntime → IPC transport → daemon → reply

This is the "hello world" of Nyrqis native applications: a program
that runs inside the NyRuntime, makes an IPC call to the backend
daemon, and processes the response.

Usage:
    python3 status_client.py [--socket /run/nyrqis/status.sock]

If the daemon is not running, the app still executes successfully
but the IPC call times out (the reply log will be empty).
"""

import argparse
import json
import os
import sys
import tempfile
import time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def build_status_client_napp() -> bytes:
    """Build a .napp binary that:
    1. LOG "Nyrqis Status Client v1.0"
    2. IPC_CALL with {"op":"ping","service":"nyrqis.backend.status"}
    3. LOG "IPC call sent, waiting for reply..."
    4. HALT with exit code 0
    """
    # Data segment layout:
    # 0..6:  "hello\0" (exit code placeholder area)
    # 6..14: "status\0" (unused padding)
    # 14..N: the IPC payload
    payload = json.dumps({
        "op": "ping",
        "service": "nyrqis.backend.status"
    }).encode("utf-8")
    payload_nul = payload + b"\x00"

    msg1 = b"Nyrqis Status Client v1.0\x00"
    msg2 = b"IPC call sent, waiting for reply...\x00"
    msg3 = b"Status client complete.\x00"

    data = bytearray()
    msg1_off = len(data)
    data.extend(msg1)
    msg2_off = len(data)
    data.extend(msg2)
    msg3_off = len(data)
    data.extend(msg3)
    payload_off = len(data)
    data.extend(payload_nul)
    # Pad to align
    data.extend(b"\x00" * 4)

    code = bytearray()
    # LOG msg1
    code.append(0x06)  # OP_LOG
    code.append(msg1_off & 0xFF)
    # IPC_CALL: service=0, op=0, payload=payload_off
    code.append(0x02)  # OP_IPC_CALL
    code.append(0x00)  # service_idx (ignored, routing is JSON)
    code.append(0x00)  # op_idx (ignored, routing is JSON)
    code.append(payload_off & 0xFF)
    # LOG msg2
    code.append(0x06)  # OP_LOG
    code.append(msg2_off & 0xFF)
    # SET_STATE key="last_call" val="ping"
    key_str = b"last_call\x00"
    val_str = b"ping\x00"
    key_off = len(data)
    data.extend(key_str)
    val_off = len(data)
    data.extend(val_str)
    code.append(0x07)  # OP_SET_STATE
    code.append(key_off & 0xFF)
    code.append(val_off & 0xFF)
    # LOG msg3
    code.append(0x06)  # OP_LOG
    code.append(msg3_off & 0xFF)
    # HALT with exit code 0
    code.append(0x00)  # OP_HALT

    # Build manifest
    manifest = json.dumps({
        "name": "status-client",
        "version": "1.0.0",
        "description": "Nyrqis status client — pings the daemon via IPC",
        "capabilities": ["ipc"],
        "entry": 0,
    }).encode("utf-8")

    # Build .napp binary
    buf = bytearray()
    buf.extend(b"NYAP")           # magic
    buf.append(1)                 # version
    buf.extend(len(manifest).to_bytes(4, 'little'))
    buf.extend(len(code).to_bytes(4, 'little'))
    buf.extend(len(data).to_bytes(4, 'little'))
    buf.extend(manifest)
    buf.extend(code)
    buf.extend(bytes(data))

    return bytes(buf)


def main():
    parser = argparse.ArgumentParser(description="Nyrqis status client")
    parser.add_argument("--socket", default="/run/nyrqis/status.sock",
                        help="Daemon socket path")
    parser.add_argument("--output", default=None,
                        help="Output .napp file path")
    args = parser.parse_args()

    napp = build_status_client_napp()

    if args.output:
        with open(args.output, "wb") as f:
            f.write(napp)
        print(f"Built {args.output} ({len(napp)} bytes)")
        return

    # Try to run it through the runtime
    try:
        from backend.nyruntime import NyRuntime
        with NyRuntime() as rt:
            # Wire IPC if socket exists
            if os.path.exists(args.socket):
                rt.set_ipc(args.socket)
                print(f"IPC wired to {args.socket}")
            else:
                print(f"Note: {args.socket} not found, IPC will timeout")

            rt.load_napp(napp)
            exit_code = rt.execute()
            print(f"Exit code: {exit_code}")

            # Show log entries
            logs = rt.log_entries()
            if logs:
                print(f"\nLog entries ({len(logs)}):")
                for i, entry in enumerate(logs):
                    level = "INFO" if entry.level == 0 else "IPC"
                    print(f"  [{level}] {entry.message}")
            else:
                print("No log entries")
    except ImportError:
        print("NyRuntime crate not built — writing .napp file instead")
        with tempfile.NamedTemporaryFile(suffix=".napp", delete=False) as f:
            f.write(napp)
            print(f"Wrote {f.name} ({len(napp)} bytes)")


if __name__ == "__main__":
    main()
