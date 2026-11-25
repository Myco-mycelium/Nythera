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
its git blob hash, in sorted path order. The blob hash is `git hash-object`
with the repository's own clean filters, so the digest is line-ending
independent: a Windows checkout writes CRLF, a Linux checkout writes LF, and
both hash the same committed content. An uncommitted source edit still moves
it exactly like a commit would. `dirty` counts all porcelain entries in the
repo, so a docs edit shows as dirty=1 while `kernel` stays put -- which is the
right answer: the proof covers the kernel, not the docs.

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
    """SHA-256 over (path, git blob hash) of the kernel source set, sorted.

    `git hash-object --stdin-paths` applies the repository's clean filters, so
    the worktree's CRLF (Windows) or LF (Linux) is hashed as the committed
    content: the same sources digest equally on either checkout. Binary files
    have no text conversion, so their bytes still count exactly.
    """
    rels = kernel_sources()
    blobs = subprocess.run(
        ["git", "hash-object", "--stdin-paths"],
        cwd=ROOT,
        input="\n".join(rels) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    digest = hashlib.sha256()
    for rel, blob in zip(rels, blobs):
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(blob))
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
