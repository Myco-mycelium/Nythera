#!/usr/bin/env python3
"""Build a Nyrqis application binary (.napp format).

Binary format:
    [magic: 4 bytes]      "NYAP"
    [entry: 4 bytes]      Entry point offset (little-endian u32)
    [code_len: 4 bytes]   Code segment length (little-endian u32)
    [data_len: 4 bytes]   Data segment length (little-endian u32)
    [code: code_len bytes] Code segment
    [data: data_len bytes] Data segment

Opcodes:
    0x00 = HALT (exit with data[0] as code)
    0x01 = NOP  (no operation, advance PC)
    0x02 = PRINT (print data[data[pc+1]..data[pc+2]])

Usage:
    python3 tools/build_napp.py --output hello.napp --data 0
    python3 tools/build_napp.py --output test.napp --code 01,01,01,00 --data 42
"""

import argparse
import struct
import sys
from pathlib import Path


MAGIC = b"NYAP"


def build_napp(code: bytes, data: bytes, entry: int = 0) -> bytes:
    """Build a .napp binary."""
    header = struct.pack("<4sIII", MAGIC, entry, len(code), len(data))
    return header + code + data


def main():
    parser = argparse.ArgumentParser(description="Build a Nyrqis application binary")
    parser.add_argument("--output", "-o", required=True, help="Output .napp file")
    parser.add_argument("--entry", type=int, default=0, help="Entry point offset")
    parser.add_argument(
        "--code",
        default="00",
        help="Code segment as comma-separated hex bytes (default: 0x00 = HALT)",
    )
    parser.add_argument(
        "--data",
        default="0",
        help="Data segment as comma-separated decimal bytes (default: 0)",
    )
    args = parser.parse_args()

    # Parse code
    code = bytes(int(x.strip(), 16) for x in args.code.split(",") if x.strip())

    # Parse data
    data = bytes(int(x.strip()) for x in args.data.split(",") if x.strip())

    napp = build_napp(code, data, args.entry)

    Path(args.output).write_bytes(napp)
    print(f"Built {args.output}: {len(napp)} bytes (code={len(code)}, data={len(data)})")


if __name__ == "__main__":
    main()
