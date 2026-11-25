#!/usr/bin/env python3
"""
source_state.py — one line describing the kernel sources a run is about to use.

build.sh and tools/qemu_boot_test.sh print this line into their own logs, and
tools/make_progress_zip.py reads it back. A log proves the sources it names
only if the tree still hashes the same, which is a content question no file
mtime can answer: a restored file, a fresh checkout and an untouched file all
carry mtimes that say nothing about what the run saw.

Format (one line, fixed key order, no timestamp):

    SOURCE_STATE commit=<sha> dirty=<n> kernel=<sha256>

`kernel` covers every file under nykernel-package/source/nykernel that git
tracks or leaves untracked-but-not-ignored, minus *.log: each file's path and
the SHA-256 of its bytes, in sorted path order. An uncommitted source edit
moves it exactly like a commit would. `dirty` counts all porcelain entries in
the repo, so a docs edit shows as dirty=1 while `kernel` stays put -- which is
the right answer: the proof covers the kernel, not the docs.

Usage:
    python3 tools/source_state.py
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL_REL = "nykernel-package/source/nykernel"


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    ).stdout


def kernel_sources():
    """Relative paths of the kernel's source set, sorted."""
    listing = git(
        "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", KERNEL_REL
    ).decode("utf-8", "surrogateescape")
    return sorted(
        rel for rel in listing.split("\0") if rel and not rel.endswith(".log")
    )


def kernel_digest():
    digest = hashlib.sha256()
    for rel in kernel_sources():
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(hashlib.sha256((ROOT / rel).read_bytes()).digest())
    return digest.hexdigest()


def dirty_count():
    return len(git("status", "--porcelain").decode("utf-8", "replace").splitlines())


def state_line():
    commit = git("rev-parse", "HEAD").decode().strip()
    return f"SOURCE_STATE commit={commit} dirty={dirty_count()} kernel={kernel_digest()}"


if __name__ == "__main__":
    try:
        print(state_line())
    except (OSError, subprocess.CalledProcessError) as exc:
        # Provenance must never break a build or a gate run; say so instead.
        print(f"SOURCE_STATE unavailable ({exc})")
        sys.exit(0)
