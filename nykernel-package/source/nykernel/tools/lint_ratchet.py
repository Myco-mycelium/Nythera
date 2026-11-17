#!/usr/bin/env python3
"""lint_ratchet.py -- the shadowing gate's mechanism, applied to the lints
whose failure mode is an abort or a silently wrong number.

    ./tools/lint_ratchet.py             # check (exit 1 on any growth)
    ./tools/lint_ratchet.py --list      # print every site, no verdict
    ./tools/lint_ratchet.py --update    # rewrite the baseline (review it!)
    ./tools/lint_ratchet.py --update --allow-grow

WHY THESE LINTS

The shadowing gate exists because one class of mistake -- a local that
re-binds a live name -- printed a plausible number that was not the measured
one. The same shape of harm comes from three more, all in clippy's
`restriction` group, i.e. OFF by default, so clippy's silence never meant
anything about them:

  unwrap_used       a `None`/`Err` path panics; in this kernel a panic is an
                    abort, which in a proof gate looks like a hung QEMU run
                    and not like the wrong answer it is.
  expect_used       the same, with a message that looks like evidence.
  indexing_slicing  an index past a table is a panic, and in a table-driven
                    dispatcher it is one `as usize` away from being a read of
                    the wrong row -- the block-info bug's shape again.

Each is a ratchet, not a clean-up: the sites that exist are recorded in
tools/lint_baseline.txt per file, and only GROWTH fails the build.

WHAT IS DELIBERATELY NOT HERE

`as_conversions` (842 sites in src/, 530 of them in experimental/) and
`arithmetic_side_effects` (404 + 482) are real truncation/wrap hazards, but
ratcheting them means baselining ~2000 lines, and a baseline nobody can read
is not a review. They need a conversion pass first; until then this file's
absence of them is a decision, not an oversight.

SCOPE

src/, excluding src/porting/ (vendored, not this tree's style) and
src/experimental/ (demos and proofs, where `expect` on a boot-only invariant
is the intended idiom). Experimental counts are printed as information, never
gated. Those exclusions are the same ones tools/shadow_check.sh uses, so a
reviewer sees one scope rule, not two.
"""

import json
import os
import shutil
import subprocess
import sys

LINTS = ("unwrap_used", "expect_used", "indexing_slicing")
BASELINE = "tools/lint_baseline.txt"
SKIP_DIRS = ("/porting/", "/experimental/")
GROW_MAX = int(os.environ.get("LINT_GROW_MAX", "2"))
REQUIRED = os.environ.get(
    "NYRQIS_STYLE_REQUIRED", os.environ.get("SHADOW_CHECK_REQUIRED", "0")
) == "1"


def skip(reason: str) -> "NoReturn":  # noqa: F821 - doc-only annotation
    if REQUIRED:
        print(f"lint ratchet: FAILED -- {reason}")
        print("              NYRQIS_STYLE_REQUIRED=1, so a skip is a failure here.")
        print("              install it with: rustup component add clippy")
        sys.exit(1)
    print(f"lint ratchet: SKIPPED -- {reason}")
    print("              (optional check; NYRQIS_STYLE_REQUIRED=1 makes this fatal)")
    sys.exit(0)


def run_clippy():
    cmd = ["cargo", "clippy", "-Zjson-target-spec", "--message-format=json", "--"]
    # Two argv elements per lint: rustc takes `-W` and the lint name
    # separately. Passing "-W clippy::x" as ONE element makes it split on the
    # space and report `unknown lint tool: \` clippy\`` -- which is exactly the
    # kind of quietly-wrong answer this file exists to catch, in the gate
    # itself.
    for lint in LINTS:
        cmd += ["-W", f"clippy::{lint}"]
    env = dict(os.environ, RUSTC_BOOTSTRAP="1")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    sites = {}
    info = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-message":
            continue
        diag = msg.get("message", {})
        code = (diag.get("code") or {}).get("code") or ""
        if not code.startswith("clippy::"):
            continue
        lint = code.split("::", 1)[1]
        if lint not in LINTS:
            continue
        spans = [s for s in diag.get("spans", []) if s.get("is_primary")]
        if not spans:
            continue
        path = spans[0]["file_name"]
        if not path.startswith("src/"):
            continue
        if any(d in path for d in SKIP_DIRS):
            info[(lint, path)] = info.get((lint, path), 0) + 1
            continue
        sites[(lint, path)] = sites.get((lint, path), 0) + 1
    # A hard compile error is not a lint verdict; say which it is.
    if proc.returncode != 0 and not sites:
        errors = [l for l in proc.stderr.splitlines() if l.startswith("error")]
        if errors:
            print("lint ratchet: FAILED -- clippy could not analyse the crate:")
            for line in errors[:8]:
                print("    " + line)
            sys.exit(1)
    return sites, info


def read_baseline():
    known = {}
    if not os.path.exists(BASELINE):
        return known
    for line in open(BASELINE, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        lint, path, count = parts
        known[(lint, path)] = int(count)
    return known


def write_baseline(sites):
    with open(BASELINE, "w", encoding="utf-8") as out:
        out.write("# Known, reviewed sites of the abort/truncation lints:\n")
        out.write("# <lint> <path> <occurrences>, maintained by\n")
        out.write("# tools/lint_ratchet.py --update. SCOPE: src/ minus\n")
        out.write("# src/porting/ (vendored) and src/experimental/ (demos and\n")
        out.write("# proofs, where a boot-only `expect` is the intended idiom).\n")
        out.write("# GROWTH IS A CODE CHANGE: a new line here, or a raised\n")
        out.write("# count, has to be justified in the commit message.\n")
        for (lint, path) in sorted(sites):
            out.write(f"{lint}\t{path}\t{sites[(lint, path)]}\n")


def main(argv):
    mode = ""
    allow_grow = False
    for arg in argv:
        if arg == "--update":
            mode = "update"
        elif arg == "--list":
            mode = "list"
        elif arg == "--allow-grow":
            allow_grow = True
        else:
            print("usage: lint_ratchet.py [--update [--allow-grow]|--list]")
            return 2

    if shutil.which("cargo") is None:
        skip("cargo is not on PATH")
    probe = subprocess.run(
        ["cargo", "clippy", "--version"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        skip("clippy is not installed for this toolchain")

    sites, info = run_clippy()
    total = sum(sites.values())

    if mode == "list":
        print(f"lint ratchet: {len(sites)} file/lint pair(s), {total} site(s) in scope")
        for (lint, path) in sorted(sites):
            print(f"    {lint:18s} {path:48s} {sites[(lint, path)]}")
        if info:
            out_of_scope = sum(info.values())
            print(f"    ({out_of_scope} more site(s) in experimental/ and porting/, not gated)")
        return 0

    if mode == "update":
        known = read_baseline()
        added = {k: v for k, v in sites.items() if v > known.get(k, 0)}
        added_n = len(added)
        if added_n > GROW_MAX and not allow_grow:
            print(f"lint ratchet: REFUSED -- this would add {added_n} new site(s)")
            print(f"              (baseline has {len(known)}; LINT_GROW_MAX={GROW_MAX}).")
            print("              Rename/fix them, or pass --allow-grow if reviewed.")
            for key in sorted(added):
                print(f"    + {key[0]}: {key[1]} {known.get(key, 0)} -> {added[key]}")
            return 1
        write_baseline(sites)
        print(f"lint ratchet: baseline rewritten with {len(sites)} pair(s) -> {BASELINE}")
        for key in sorted(added):
            print(f"    + {key[0]}: {key[1]} {known.get(key, 0)} -> {added[key]}")
        return 0

    known = read_baseline()
    if not known and os.path.exists(BASELINE) is False:
        print(f"lint ratchet: FAILED -- {BASELINE} is missing.")
        print("              Run: ./tools/lint_ratchet.py --update (and review it)")
        return 1

    grown = []
    for key, count in sites.items():
        was = known.get(key, 0)
        if count > was:
            grown.append((key, was, count))
    gone = [key for key in known if key not in sites or sites[key] < known[key]]
    for key in sorted(gone):
        print(f"lint ratchet: note -- {key[0]} {key[1]}: "
              f"{known[key]} -> {sites.get(key, 0)} (run --update to tighten)")
    if not grown:
        print(f"lint ratchet: OK -- {total} site(s) in {len(sites)} file(s), "
              f"none above the baseline")
        return 0
    print("lint ratchet: FAILED -- NEW abort/truncation sites:")
    for (lint, path), was, now in sorted(grown):
        print(f"    {path}: {lint} {was} -> {now} site(s)")
    print("""
These are the panics and index/slice hazards that turn a wrong answer into an
abort (or into a read of the wrong table row) before any verdict can be
printed. Fix the site -- or, if it cannot fail by construction and the
argument is in the code, record it:

    ./tools/lint_ratchet.py --update && git diff tools/lint_baseline.txt
""")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
