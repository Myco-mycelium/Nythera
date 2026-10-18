#!/usr/bin/env python3
"""
patch_weak_verneed.py -- sets VER_FLG_WEAK (0x2) on the vernaux aux
entry of `userprog/userprog_weak.elf`'s `.gnu.version_r` section.
+
Context: this environment's GNU ld records a weak symbol bind
(dynsym STB_WEAK) faithfully but NEVER sets VER_FLG_WEAK on the verneed
aux entry, even when every bind in that version's dependency is weak.
The kernel's weak requirement rule has TWO halves -- weak BY BIND and
weak BY FLAG -- and this artifact is the test vector for the flag
half, so the build composes the flag onto the file deliberately (the
loader-parity this models is glibc's _dl_lookup handling of a weak
VERSION dependency: non-fatal when unsatisfiable). The readelf layout
gate refuses the build unless the patched file literally carries
`Flags: WEAK`, so the patch is verified, not assumed. A toolchain that
ever emits the flag naturally passes through this step as a no-op.
"""

import re
import struct
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <elf-file>", file=sys.stderr)
        return 2
    path = sys.argv[1]

    out = subprocess.run(
        ["readelf", "-V", path], capture_output=True, text=True, check=True
    ).stdout
    # DOTALL: the `Addr:/Offset:` line is the one AFTER the section
    # title line, so the lazy `.*?` has to cross a newline to reach it.
    m = re.search(
        r"Version needs section[^:]*:.*?Addr: (0x[0-9a-f]+)\s+Offset: (0x[0-9a-f]+)",
        out,
        re.DOTALL,
    )
    if not m:
        print(f"no .gnu.version_r section found in {path}", file=sys.stderr)
        return 1
    sec_off = int(m.group(2), 16)

    # IDEMPOTENCE: if the flag is already set, this is a verified
    # no-op -- a second run (or a future toolchain that emits the flag
    # naturally) must not re-write the file at all. The verification
    # is the same readelf self-check the patch path performs below,
    # so both paths return only what readelf can literally confirm.
    if "Flags: WEAK" in out:
        print(
            f"  weak-verneed patch: {path} already carries Flags: WEAK (idempotent no-op, verified)"
        )
        return 0

    with open(path, "rb") as f:
        data = bytearray(f.read())

    # Verneed struct at the section start: vn_version(2) vn_cnt(2)
    # vn_file(4) vn_aux(4->offset of the FIRST aux entry) vn_next(4).
    (vn_version, vn_cnt, _vn_file, vn_aux, _vn_next) = struct.unpack_from(
        "<HHIII", data, sec_off
    )
    assert vn_version == 1, f"unexpected verneed version {vn_version}"
    assert vn_aux >= 0x10, f"aux offset {vn_aux:#x} is inside the verneed entry"
    aux_off = sec_off + vn_aux
    flags_off = aux_off + 4  # vna_flags: u16 LE
    old = struct.unpack_from("<H", data, flags_off)[0]
    if old & 0x0002:
        # Belt-and-suspenders: the readelf text said unflagged but the
        # struct says flagged. Trust the STRUCT (it is what the kernel
        # reads), report the contradiction, still verify via readelf.
        print(
            f"  weak-verneed patch: {path} struct says VER_FLG_WEAK set ({old:#x}) but readelf text disagrees; verifying"
        )
    new = old | 0x0002  # VER_FLG_WEAK
    struct.pack_into("<H", data, flags_off, new)

    with open(path, "wb") as f:
        f.write(data)

    # Self-check: the patched file must literally report the weak flag.
    out2 = subprocess.run(
        ["readelf", "-V", path], capture_output=True, text=True, check=True
    ).stdout
    if old == new:
        # Nothing changed (flag already set in the struct but the
        # readelf text disagreed above): still refuse unless the file
        # verifies, so a contradictory input cannot pass silently.
        print(
            f"  weak-verneed patch: {path} struct flag already set; no bytes written (contradiction reported above)"
        )
    if "Flags: WEAK" not in out2:
        print(
            f"patch not visible in readelf output of {path} (expected Flags: WEAK)",
            file=sys.stderr,
        )
        return 1
    print(f"  weak-verneed patch: {path} vernaux flags {old:#x} -> {new:#x} (Flags: WEAK verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
