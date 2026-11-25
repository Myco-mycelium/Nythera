#!/usr/bin/env python3
"""
make_progress_zip.py — package the working tree into a snapshot archive that
carries its own proof.

Why this exists: the progress snapshots handed around during the live-ISO
work were assembled by hand, so the archive that reached a reviewer had no
record of the gate run or the build that produced it, and two people could
not produce the same bytes from the same commit. This script writes the
snapshot in one deterministic pass: fixed entry order, fixed timestamps,
fixed compression level, and the gate transcript and build log copied in
under `Nythera/proof/` with their hashes recorded in `Nythera/PROGRESS.txt`.

What goes in:
  * the full working tree at HEAD plus any uncommitted edits (the docs edit
    that predates this snapshot included), minus git internals, build output
    (`target/`), generated ISO artifacts (`artifacts/`) and caches;
  * `nythera-history.bundle` — the local branch history, so the snapshot can
    be cloned, not just read;
  * `proof/gate.log` — the stdout of the last `tools/qemu_boot_test.sh` run:
    the guest transcript plus the PASS/FAIL verdict, which the gate prints to
    its own stdout (target/qemu-test-stdout.log alone carries no verdict);
  * `proof/build.log` — the last `build.sh` run;
  * `PROGRESS.txt` recording the commit, both proof hashes, the gate verdict
    and what was excluded.

Usage (in nykernel-package/source/nykernel):
    bash build.sh > target/build.log 2>&1        # the build proof
    bash tools/qemu_boot_test.sh > target/gate.log 2>&1   # the gate proof
    python3 tools/make_progress_zip.py [--output PATH]
                                       [--gate-log PATH] [--build-log PATH]

On a Windows host, source tools/win_toolchain.sh first so QEMU and the
cross binutils are on PATH, as IMPLEMENTATION_STATUS.md describes.

Staleness: a log proves only the sources it actually saw, so the gate and the
build each print a SOURCE_STATE line into their log (see tools/source_state.py)
carrying the commit, the repo's dirty count and a digest of the kernel source
set. This script recomputes that digest and warns for any log whose line names
a different one, or that has no line at all. Content answers the question
mtimes cannot: a fresh checkout, a restored file and an untouched file carry
mtimes that say nothing about what a run saw. What the digest still cannot
show is whether the run exercised those sources -- only that the tree it ran
against is the tree in the archive.

Reproducibility: the same tree and the same two logs produce the same zip
bytes on the same machine. Entry names are sorted, every entry carries the
1980 epoch and its source file mode, no host names or absolute paths are
stored, nothing reads the clock, and the history bundle is pinned: it packs
branches and tags only (not `--all`, whose extra refs/freebuff/landing/* refs
move without a commit) with `pack.threads=1`, because pack-objects' threaded
delta search is not reproducible -- one bundle in 25 came out 3 bytes larger
than the other 24. The compressed bytes still depend on the zlib build.

The proof logs are inputs, not checks: this script does not run the gate or
the build, and a `FAIL:` verdict in the transcript is copied and reported
rather than hidden.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import source_state

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "nykernel-package" / "source" / "nykernel"
DEFAULT_GATE_LOG = KERNEL / "target" / "gate.log"
DEFAULT_BUILD_LOG = KERNEL / "target" / "build.log"

# tools/source_state.py's line, as it appears in a log. Last match wins: a log
# that prints it twice is still described by what it last said.
STATE_RE = re.compile(
    r"^SOURCE_STATE commit=(\S+) dirty=(\d+) kernel=([0-9a-f]{64})$", re.MULTILINE
)

# Build output, generated discs and caches: reproducible from the sources in
# the archive, and together several hundred megabytes larger than they are
# worth here. `target` covers both the kernel build and any Rust crate's.
SKIP_DIRS = {
    "target",
    "artifacts",
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    ".venv-test",
    ".venv-docs",
}

EPOCH = (1980, 1, 1, 0, 0, 0)


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def gate_verdict(gate_log):
    """The last PASS/FAIL line of the gate's own stdout, or None."""
    for line in reversed(gate_log.decode("utf-8", "replace").splitlines()):
        if line.startswith(("PASS:", "FAIL:")):
            return line
    return None


def tree_files(output_rel):
    """Every file to pack, as (archive name, path, mode), sorted by name."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(ROOT).as_posix()
            if rel == output_rel:
                continue
            # A previous snapshot in the root, never packed into the next one.
            if name.startswith("Nythera-progress-") and name.endswith(".zip"):
                continue
            if rel == ".git" or not path.is_file():
                continue
            entries.append((f"Nythera/{rel}", path, path.stat().st_mode & 0o777))
    entries.sort(key=lambda entry: entry[0])
    return entries


def staleness_warnings(gate_log_path, build_log_path):
    """WARN lines for each proof log whose recorded kernel digest is not this tree's."""
    current = source_state.kernel_digest()
    warnings = []
    for label, path in (("gate log", gate_log_path), ("build log", build_log_path)):
        log = path.relative_to(ROOT).as_posix()
        states = STATE_RE.findall(path.read_text(encoding="utf-8", errors="replace"))
        if not states:
            warnings.append(
                f"WARN: the {label} {log} carries no SOURCE_STATE line, so the "
                f"sources it ran against cannot be compared with this tree; "
                f"re-run it before handing the snapshot out"
            )
        elif states[-1][2] != current:
            warnings.append(
                f"WARN: the {label} {log} ran against kernel {states[-1][2][:12]} "
                f"(dirty={states[-1][1]}) but this tree hashes {current[:12]} -- "
                f"the run never saw these sources; re-run it before handing the "
                f"snapshot out"
            )
    return warnings


def progress_text(head, branch, subject, committed, kernel_digest, gate_log, build_log, verdict):
    return f"""Nythera progress snapshot
=========================
commit:    {head}
branch:    {branch}
subject:   {subject}
committed: {committed}

Proof carried in this archive
-----------------------------
kernel sources: {kernel_digest}
  (the SOURCE_STATE kernel= the proof logs were taken under; it hashes the
  kernel source set's git-normalised content, so a Windows CRLF checkout and
  a Linux LF checkout of these sources agree. Compare it with
  `python3 tools/source_state.py` before trusting either log.)

gate output: proof/gate.log
  source:  nykernel-package/source/nykernel/target/gate.log
  bytes:   {len(gate_log)}
  sha256:  {sha256(gate_log)}
  verdict: {verdict}

build log: proof/build.log
  source:  nykernel-package/source/nykernel/target/build.log
  bytes:   {len(build_log)}
  sha256:  {sha256(build_log)}

The proof files are the logs of runs made on the snapshot machine; this
archive does not re-run them. Check one with:
  sha256sum proof/gate.log   (or: shasum -a 256)

Contents
--------
The full working tree at the commit above, with any uncommitted edits
applied (including docs/how-to/boot-the-live-iso.md when it is dirty), plus
nythera-history.bundle holding the local branch history. Excluded: git
internals, build output (target/), generated discs (artifacts/) and caches.

Restore
-------
  git clone nythera-history.bundle Nythera
  # or inspect the tree as packed, without git:
  unzip -l Nythera-progress-*.zip | head
"""


def write_entry(archive, arcname, data, mode):
    info = zipfile.ZipInfo(arcname, date_time=EPOCH)
    info.create_system = 3  # unix, so the mode below reads back the same way
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=6)


def main():
    head = git("rev-parse", "HEAD").strip()
    short = git("rev-parse", "--short", "HEAD").strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    subject = git("log", "-1", "--format=%s").strip()
    committed = git("log", "-1", "--format=%cI").strip()

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / f"Nythera-progress-{short}.zip",
        help="archive to write (default: Nythera-progress-<short sha>.zip at the repo root)",
    )
    parser.add_argument(
        "--gate-log",
        type=Path,
        default=DEFAULT_GATE_LOG,
        help="gate stdout to embed, transcript plus verdict (default: %(default)s)",
    )
    parser.add_argument(
        "--build-log",
        type=Path,
        default=DEFAULT_BUILD_LOG,
        help="build log to embed (default: %(default)s)",
    )
    args = parser.parse_args()

    for path, why in (
        (args.gate_log, "run `bash tools/qemu_boot_test.sh > target/gate.log 2>&1` in nykernel-package/source/nykernel"),
        (args.build_log, "run `bash build.sh > target/build.log 2>&1` there"),
    ):
        if not path.is_file():
            raise SystemExit(f"FAIL: no proof at {path} -- {why}")

    for warning in staleness_warnings(args.gate_log, args.build_log):
        print(warning)

    gate_log = args.gate_log.read_bytes()
    build_log = args.build_log.read_bytes()
    verdict = gate_verdict(gate_log)
    if verdict is None:
        raise SystemExit(
            f"FAIL: {args.gate_log} has no PASS:/FAIL: line; the gate prints its "
            f"verdict to its own stdout, so capture the run, not just the "
            f"transcript it writes"
        )

    output = args.output.resolve()
    try:
        output_rel = output.relative_to(ROOT).as_posix()
    except ValueError:
        output_rel = ""  # outside the tree: nothing to exclude

    entries = tree_files(output_rel)

    # The history bundle is built in a temp file: `git bundle create` wants a
    # new file, and the zip only needs its bytes. Branches and tags only, with
    # one pack thread: see the module docstring for why both are pinned.
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "nythera-history.bundle"
        subprocess.run(
            ["git", "-c", "pack.threads=1", "bundle", "create", str(bundle),
             "--branches", "--tags"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        bundle_bytes = bundle.read_bytes()

    files = [
        (
            "Nythera/PROGRESS.txt",
            progress_text(
                head,
                branch,
                subject,
                committed,
                source_state.kernel_digest(),
                gate_log,
                build_log,
                verdict,
            ).encode("utf-8"),
            0o644,
        ),
        ("Nythera/proof/gate.log", gate_log, 0o644),
        ("Nythera/proof/build.log", build_log, 0o644),
        ("Nythera/nythera-history.bundle", bundle_bytes, 0o644),
    ]
    files += [(arcname, path.read_bytes(), mode) for arcname, path, mode in entries]
    files.sort(key=lambda entry: entry[0])

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for arcname, data, mode in files:
            write_entry(archive, arcname, data, mode)

    size = output.stat().st_size
    print(f"OK: {output} ({len(files)} entries, {size} bytes)")
    print(f"    commit {head}")
    print(f"    gate   {verdict}")
    print(f"    proof  gate {sha256(gate_log)[:16]}…, build log {sha256(build_log)[:16]}…")
    print(f"    zip    {sha256(output.read_bytes())[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
