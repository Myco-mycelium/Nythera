#!/usr/bin/env python3
"""
check_svc84_ledger.py -- the 0x84 numbering gate.

The surface in `src/service84.rs` is numbered into a range the release's
own ledger never uses. It has to be: the release's ledger spans 0..28
(yield=0, SEND=1, RECV=2, exit=3, spawn=4, ... fb_info=20 ... REVOKE=25,
SYSINFO=26, PROCINFO=27, PROC_KILL=28), and the surface's FIRST version
numbered itself 0..4 -- colliding at every served number. A release-typed
program's `yield` (0) would have been ANSWERED with `INPUT_STATE` instead
of refused with -ENOSYS, which is exactly the "answered what was not
ported" lie the refusal convention exists to prevent. The step-3 shell
survey found it; the numbers were moved to 0x40..; this gate pins both
sides so neither can drift back into the other:

  1. DISJOINT served calls must not appear anywhere in the release's
     ledger (the syscall_demo dispatch arms + every release user program's
     rax-loads before `int $0x84`).
  2. COVERED the ring-3 proof (userprog/prog_svc84.s, its peer
     userprog/prog_svc84_peer.s, and the coordinator in
     src/svc84_proof.rs) must exercise EVERY served call number, so
     renumbering without updating the proof fails the build instead of
     quietly proving a call that no longer exists.

Reads files, prints one summary line, refuses the build by exiting 1 with
the named failure. Usage: python3 tools/check_svc84_ledger.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE84 = ROOT / "src" / "service84.rs"
RELEASE_DEMO = ROOT / "porting" / "release-1-71" / "src" / "experimental" / "syscall_demo.rs"
RELEASE_PROGS = ROOT / "porting" / "release-1-71" / "userprog"
PROOF_PROG = ROOT / "userprog" / "prog_svc84.s"
# The proof is TWO ring-3 processes and one kernel COORDINATOR since
# 2026-11-06: the peer process drives the rows only it can reach (the
# receive and reply that answer the parked caller), and the verdict call
# (PROOF_DONE) is made by the coordinator on task 0, because the region's
# teardown is only observable after both processes have EXITed. A row
# exercised from either of those is exercised -- so this gate reads all
# three files rather than pretending the first program still does
# everything.
PEER_PROG = ROOT / "userprog" / "prog_svc84_peer.s"
COORDINATOR = ROOT / "src" / "svc84_proof.rs"


def served_calls() -> dict[str, int]:
    out = {}
    for m in re.finditer(r"pub const (CALL_[A-Z_0-9]+): u64 = (0x[0-9a-fA-F_]+|\d+);", SERVICE84.read_text()):
        out[m.group(1)] = int(m.group(2).replace("_", ""), 0)
    if not out:
        sys.exit("FAIL: no CALL_* constants found in src/service84.rs -- the parser drifted")
    return out


def release_ledger() -> set[int]:
    # The dispatcher's arms, indented 12 spaces inside the match on nr.
    nums: set[int] = set()
    src = RELEASE_DEMO.read_text()
    for m in re.finditer(r"^ {12}(\d+(?:\s*\|\s*\d+)*)\s*=>", src, re.M):
        for tok in m.group(1).split("|"):
            nums.add(int(tok.strip()))
    # And every release-typed user program: the rax load nearest above each
    # `int $0x84` is that call's number.
    for prog in sorted(RELEASE_PROGS.glob("*.s")):
        text = prog.read_text()
        for m in re.finditer(r"int\s+\$0x84", text):
            loads = re.findall(r"mov\s+\$([0-9]+|0x[0-9a-fA-F]+),\s*%rax", text[: m.start()])
            if loads:
                nums.add(int(loads[-1], 0))
    if not nums:
        sys.exit("FAIL: the release ledger parsed empty -- paths or parser drifted (demo not found?)")
    return nums


def proof_numbers() -> set[int]:
    nums: set[int] = set()
    asm_rows = 0
    for prog in (PROOF_PROG, PEER_PROG):
        text = prog.read_text()
        for m in re.finditer(r"int\s+\$0x84", text):
            loads = re.findall(r"mov\s+\$([0-9]+|0x[0-9a-fA-F]+),\s*%rax", text[: m.start()])
            if loads:
                nums.add(int(loads[-1], 0))
                asm_rows += 1
    if not asm_rows:
        sys.exit("FAIL: the proof programs parsed empty -- prog_svc84.s drifted")
    # The coordinator names the constants it dispatches (the verdict row),
    # so a served call reached that way is covered by name.
    coord = COORDINATOR.read_text()
    for name, num in served_calls().items():
        if re.search(r"\b" + name + r"\b", coord):
            nums.add(num)
    if not nums:
        sys.exit("FAIL: the proof programs parsed empty -- the proof drifted")
    return nums


def main() -> None:
    served = served_calls()
    release = release_ledger()
    proof = proof_numbers()

    collisions = sorted(set(served.values()) & release)
    if collisions:
        names = sorted(n for n, v in served.items() if v in collisions)
        sys.exit(
            f"FAIL: served call number(s) {', '.join(hex(c) for c in collisions)} "
            f"({', '.join(names)}) COLLIDE with the release's ledger -- a release-typed "
            f"program's call there would be ANSWERED instead of refused -ENOSYS. Move the "
            f"surface's numbers somewhere the release never uses."
        )

    missing = sorted(set(served.values()) - proof)
    if missing:
        sys.exit(
            f"FAIL: served number(s) "
            f"{', '.join(hex(c) for c in missing)} are not exercised by prog_svc84.s "
            f"-- the ring-3 proof no longer covers every served call."
        )

    span = f"{min(release)}..{max(release)}"
    lo, hi = hex(min(served.values())), hex(max(served.values()))
    print(
        f"svc84 ledger gate: {len(served)} served calls ({lo}..{hi}) disjoint from the "
        f"release ledger ({span}, {len(release)} numbers over syscall_demo.rs + "
        f"{len(list(RELEASE_PROGS.glob('*.s')))} release programs); "
        f"prog_svc84.s + prog_svc84_peer.s + the task-0 coordinator exercise every served call"
    )


if __name__ == "__main__":
    main()
