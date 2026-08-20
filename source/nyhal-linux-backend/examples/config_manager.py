#!/usr/bin/env python3
"""
config_manager.py — Nyrqis config management application.

Demonstrates the full Nyrqis stack with a real-world use case:
1. Writes a default config file (FS_WRITE)
2. Reads it back (FS_READ → state store)
3. Logs the configuration (LOG)
4. Reports status to daemon via IPC (IPC_CALL)
5. Persists state for the next run (SET_STATE)

Usage:
    python3 config_manager.py [--output /tmp/config.napp]
    python3 config_manager.py  # run with NyRuntime (requires crate)
"""

import argparse
import json
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def build_config_manager_napp(
    config_path: str = "/tmp/nyrqis-config.json",
    report_path: str = "/tmp/nyrqis-config-report.txt",
    ipc_payload: dict = None,
) -> bytes:
    """Build a .napp binary that manages configuration.

    Opcodes executed:
      1. LOG "Config Manager v1.0 starting..."
      2. FS_WRITE config_path → default_config_data
      3. LOG "Config written to /tmp/nyrqis-config.json"
      4. FS_READ config_path → state_key "config_content"
      5. LOG "Config read back successfully"
      6. SET_STATE "config_version" = "1.0.0"
      7. SET_STATE "config_status" = "loaded"
      8. IPC_CALL → {"op": "status", "service": "nyrqis.backend.status"}
      9. FS_WRITE report_path → report_data
      10. LOG "Config Manager complete"
      11. HALT 0
    """
    if ipc_payload is None:
        ipc_payload = {"op": "status", "service": "nyrqis.backend.status"}

    # Data segment layout:
    # [0]: exit code (0)
    # [1..3]: padding
    # Then various strings and payloads

    config_data = json.dumps({
        "app": "nyrqis-config-manager",
        "version": "1.0.0",
        "settings": {
            "theme": "Eclipse",
            "font_size": 14,
            "auto_save": True,
            "max_recent_files": 10,
        },
    }, indent=2).encode("utf-8")

    report_data = json.dumps({
        "status": "success",
        "config_file": config_path,
        "config_size": len(config_data),
        "message": "Configuration loaded and verified",
    }, indent=2).encode("utf-8")

    ipc_bytes = json.dumps(ipc_payload).encode("utf-8")

    # Build data segment
    data = bytearray()
    data.append(0)  # exit code = 0
    while len(data) % 4 != 0:
        data.append(0)

    # Message strings (NUL-terminated)
    msg1 = b"Config Manager v1.0 starting...\x00"
    msg1_off = len(data)
    data.extend(msg1)

    msg2 = b"Config written to config path\x00"
    msg2_off = len(data)
    data.extend(msg2)

    msg3 = b"Config read back successfully\x00"
    msg3_off = len(data)
    data.extend(msg3)

    msg4 = b"Config Manager complete\x00"
    msg4_off = len(data)
    data.extend(msg4)

    msg5 = b"FS_READ failed for config\x00"
    msg5_off = len(data)
    data.extend(msg5)

    # Config path (NUL-terminated)
    config_path_bytes = config_path.encode("utf-8") + b"\x00"
    config_path_off = len(data)
    data.extend(config_path_bytes)

    # Report path (NUL-terminated)
    report_path_bytes = report_path.encode("utf-8") + b"\x00"
    report_path_off = len(data)
    data.extend(report_path_bytes)

    # Config content (NUL-terminated)
    config_content_off = len(data)
    data.extend(config_data + b"\x00")

    # Report content (NUL-terminated)
    report_content_off = len(data)
    data.extend(report_data + b"\x00")

    # IPC payload (NUL-terminated)
    ipc_payload_off = len(data)
    data.extend(ipc_bytes + b"\x00")

    # State keys and values
    key_version = b"config_version\x00"
    val_version = b"1.0.0\x00"
    key_status = b"config_status\x00"
    val_status = b"loaded\x00"
    key_config = b"config_content\x00"

    key_version_off = len(data)
    data.extend(key_version)
    val_version_off = len(data)
    data.extend(val_version)
    key_status_off = len(data)
    data.extend(key_status)
    val_status_off = len(data)
    data.extend(val_status)
    key_config_off = len(data)
    data.extend(key_config)

    # Ensure data alignment
    while len(data) % 4 != 0:
        data.append(0)

    # Build code segment
    code = bytearray()
    # 1. LOG msg1
    code.append(0x06)  # OP_LOG
    code.append(msg1_off & 0xFF)
    # 2. FS_WRITE config_path → config_content
    code.append(0x05)  # OP_FS_WRITE
    code.append(config_path_off & 0xFF)
    code.append(config_content_off & 0xFF)
    # 3. LOG msg2
    code.append(0x06)  # OP_LOG
    code.append(msg2_off & 0xFF)
    # 4. FS_READ config_path → state key
    code.append(0x04)  # OP_FS_READ
    code.append(config_path_off & 0xFF)
    code.append(key_config_off & 0xFF)
    # 5. LOG msg3
    code.append(0x06)  # OP_LOG
    code.append(msg3_off & 0xFF)
    # 6. SET_STATE config_version = 1.0.0
    code.append(0x07)  # OP_SET_STATE
    code.append(key_version_off & 0xFF)
    code.append(val_version_off & 0xFF)
    # 7. SET_STATE config_status = loaded
    code.append(0x07)  # OP_SET_STATE
    code.append(key_status_off & 0xFF)
    code.append(val_status_off & 0xFF)
    # 8. IPC_CALL → daemon status
    code.append(0x02)  # OP_IPC_CALL
    code.append(0x00)  # service_idx
    code.append(0x00)  # op_idx
    code.append(ipc_payload_off & 0xFF)
    # 9. FS_WRITE report_path → report_content
    code.append(0x05)  # OP_FS_WRITE
    code.append(report_path_off & 0xFF)
    code.append(report_content_off & 0xFF)
    # 10. LOG msg4
    code.append(0x06)  # OP_LOG
    code.append(msg4_off & 0xFF)
    # 11. HALT 0
    code.append(0x00)  # OP_HALT

    # Build manifest
    manifest = json.dumps({
        "name": "config-manager",
        "version": "1.0.0",
        "description": "Nyrqis configuration management application",
        "capabilities": ["ipc", "filesystem"],
        "entry": 0,
    }).encode("utf-8")

    # Build .napp binary
    buf = bytearray()
    buf.extend(b"NYAP")  # magic
    buf.append(1)  # version
    buf.extend(struct.pack("<I", len(manifest)))
    buf.extend(struct.pack("<I", len(code)))
    buf.extend(struct.pack("<I", len(data)))
    buf.extend(manifest)
    buf.extend(code)
    buf.extend(bytes(data))

    return bytes(buf)


def main():
    parser = argparse.ArgumentParser(description="Nyrqis config manager")
    parser.add_argument("--output", default=None,
                        help="Output .napp file path")
    parser.add_argument("--config-path", default="/tmp/nyrqis-config.json",
                        help="Config file path to manage")
    parser.add_argument("--report-path", default="/tmp/nyrqis-config-report.txt",
                        help="Report file path to write")
    parser.add_argument("--socket", default="/run/nyrqis/status.sock",
                        help="Daemon socket path")
    args = parser.parse_args()

    napp = build_config_manager_napp(
        config_path=args.config_path,
        report_path=args.report_path,
    )

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
                    level = {0: "INFO", 1: "IPC", 2: "ERROR"}.get(entry.level, "?")
                    try:
                        msg = entry.message.decode("utf-8", errors="replace")
                    except Exception:
                        msg = repr(entry.message)
                    print(f"  [{level}] {msg}")

            # Verify files
            if os.path.exists(args.config_path):
                print(f"\n✓ Config file: {args.config_path}")
                with open(args.config_path, "r") as f:
                    print(f"  Content: {f.read()[:200]}...")
            else:
                print(f"\n✗ Config file not created: {args.config_path}")

            if os.path.exists(args.report_path):
                print(f"\n✓ Report file: {args.report_path}")
                with open(args.report_path, "r") as f:
                    print(f"  Content: {f.read()[:200]}...")
            else:
                print(f"\n✗ Report file not created: {args.report_path}")

    except ImportError:
        print("NyRuntime crate not built — writing .napp file instead")
        with tempfile.NamedTemporaryFile(suffix=".napp", delete=False) as f:
            f.write(napp)
            print(f"Wrote {f.name} ({len(napp)} bytes)")


if __name__ == "__main__":
    main()
