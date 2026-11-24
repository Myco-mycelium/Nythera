#!/usr/bin/env python3
"""Restore IMAGE_SCN_MEM_WRITE on a PE32+ client's `.idata` section.

WHY THIS EXISTS

    The PE loader resolves a client's imports by WRITING each resolved address
    into that client's IAT, and it refuses an image whose `.idata` section does
    not declare `IMAGE_SCN_MEM_WRITE` (`ImportSectionNotWritable`) rather than
    granting a write the file never asked for. GNU ld used to set that flag on
    the section it synthesizes from `--enable-auto-import`; binutils 2.45 emits
    `.idata` as READONLY, so an otherwise correct client is refused at load.

    The flag is not a workaround for the loader: a section holding the IAT IS
    written at load time, and the file is describing it wrongly. Restoring the
    bit makes the artifact match what the image means, on every ld version, and
    keeps the loader's rule (never grant an undeclared write) intact.

WHAT IT CHECKS

    The file must be a PE32+ image, `.idata` must exist in a section header
    with file-backed contents, and the bit must be absent or (on a re-run)
    already present. Anything else is an error with the file's actual shape,
    not a silent no-op: a patcher that quietly did nothing would turn a linker
    change into a kernel-side import failure later.

USAGE

    tools/patch_pe_idata.py userprog/win32_console.exe
"""
import struct
import sys

IMAGE_SCN_MEM_WRITE = 0x8000_0000
PE32_PLUS_MAGIC = 0x20B
SECTION_HEADER_SIZE = 40


def fail(message: str) -> "NoReturn":
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(path: str) -> None:
    with open(path, "rb") as handle:
        data = bytearray(handle.read())

    if len(data) < 0x40 or data[0:2] != b"MZ":
        fail(f"{path}: not an MZ image")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        fail(f"{path}: no PE signature at e_lfanew {e_lfanew:#x}")

    coff = e_lfanew + 4
    machine, section_count = struct.unpack_from("<HH", data, coff)
    optional_size = struct.unpack_from("<H", data, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic != PE32_PLUS_MAGIC:
        fail(f"{path}: optional-header magic {magic:#x} is not PE32+ ({PE32_PLUS_MAGIC:#x})")

    sections = optional + optional_size
    for index in range(section_count):
        header = sections + index * SECTION_HEADER_SIZE
        if header + SECTION_HEADER_SIZE > len(data):
            fail(f"{path}: section header {index} runs past the file")
        name = bytes(data[header : header + 8]).rstrip(b"\0")
        raw_size, raw_pointer = struct.unpack_from("<II", data, header + 16)
        characteristics = struct.unpack_from("<I", data, header + 36)[0]
        if name != b".idata":
            continue
        if raw_size == 0 or raw_pointer == 0:
            fail(f"{path}: .idata has no file-backed contents (raw {raw_size:#x} at {raw_pointer:#x})")
        if characteristics & IMAGE_SCN_MEM_WRITE:
            print(f"{path}: .idata already declares IMAGE_SCN_MEM_WRITE")
            return
        struct.pack_into("<I", data, header + 36, characteristics | IMAGE_SCN_MEM_WRITE)
        with open(path, "wb") as handle:
            handle.write(data)
        print(
            f"{path}: .idata characteristics {characteristics:#010x} -> "
            f"{characteristics | IMAGE_SCN_MEM_WRITE:#010x} (IAT is written at load)"
        )
        return
    fail(f"{path}: no .idata section (the client must import kernel32.dll)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <pe-client.exe>", file=sys.stderr)
        raise SystemExit(2)
    main(sys.argv[1])
