#!/usr/bin/env python3
"""gate_receipt.py -- turn a gate transcript into a fact that can be DIFFED.

    ./tools/gate_receipt.py emit <gate> --log <transcript> [--out DIR] [--status N]
    ./tools/gate_receipt.py compare <old.json> <new.json>

WHY

A gate's transcript is prose: "33 OK / 0 FAIL", hashes, leg counts, the odd
pinned number. Prose is read once by whoever happened to look, and it cannot
notice that today's run proved two fewer legs than yesterday's while still
printing PASS. A receipt is the same facts as JSON, so the NEXT run can be
compared against this one and a shrinking verdict becomes a red check instead
of a detail nobody re-reads.

`tools/run_gate.sh` is the wrapper that produces them: it runs any gate,
tees the transcript into target/receipts/, emits the receipt, and compares it
with a reference for that gate -- the previous local run if there is one, else
the committed known-good receipt in tools/receipts_known_good/ (so a fresh
clone has something real to diff against). When neither exists it says the
comparison was SKIPPED rather than printing a reassuring nothing.

    ./tools/gate_receipt.py promote <gate> [--from DIR] [--out DIR]

`promote` copies a local receipt into tools/receipts_known_good/, trimmed to
the facts that are worth diffing (the timestamp and log digest are dropped, so
the committed file changes only when the measured verdict does).
"""

import hashlib
import json
import os
import re
import sys
import time

OK_LINE = re.compile(r"^\s*OK:", re.M)
FAIL_LINE = re.compile(r"^\s*FAIL:", re.M)
VERDICT = re.compile(r"^(PASS|FAIL):", re.M)
PASS_LINE = re.compile(r"^\s*PASS:", re.M)
LEGS = re.compile(r"carries (\d+) distinct proved leg")
TALLY = re.compile(r"(\d+) OK\s*/\s*(\d+) FAIL")
HASHES = re.compile(r"\b([0-9a-f]{8})\b")


def emit(gate, log_path, out_dir, status):
    text = open(log_path, encoding="utf-8", errors="replace").read()
    verdicts = VERDICT.findall(text)
    receipts = {
        "gate": gate,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": status,
        "verdict": verdicts[-1] if verdicts else ("FAIL" if status else "UNKNOWN"),
        "ok_lines": len(OK_LINE.findall(text)),
        "pass_lines": len(PASS_LINE.findall(text)),
        "fail_lines": len(FAIL_LINE.findall(text)),
        "log_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        "log_bytes": len(text),
    }
    legs = LEGS.search(text)
    if legs:
        receipts["legs"] = int(legs.group(1))
    tally = TALLY.search(text)
    if tally:
        receipts["tally_ok"] = int(tally.group(1))
        receipts["tally_fail"] = int(tally.group(2))
    hashes = sorted(set(HASHES.findall(text)))
    if hashes:
        receipts["hashes"] = hashes
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, gate.replace(".sh", "") + ".json")
    with open(path, "w", encoding="utf-8") as out:
        json.dump(receipts, out, indent=2, sort_keys=True)
        out.write("\n")
    print(f"receipt: {path}")
    keys = ("verdict", "ok_lines", "pass_lines", "fail_lines", "legs", "tally_ok", "tally_fail")
    print("         " + ", ".join(f"{k}={receipts[k]}" for k in keys if k in receipts))
    return receipts


def compare(old_path, new_path):
    old = json.load(open(old_path, encoding="utf-8"))
    new = json.load(open(new_path, encoding="utf-8"))
    diffs = []
    for key in ("verdict", "legs", "tally_ok", "tally_fail"):
        if key in old and key in new and old[key] != new[key]:
            if key == "verdict" and new[key] == "PASS":
                continue
            if key in ("tally_ok",) and new[key] > old[key]:
                continue
            if key in ("tally_fail", "fail_lines") and new[key] < old[key]:
                continue
            diffs.append(f"{key}: {old[key]} -> {new[key]}")
    if new["ok_lines"] < old["ok_lines"]:
        diffs.append(f"ok_lines: {old['ok_lines']} -> {new['ok_lines']} (fewer checks ran)")
    if new.get("pass_lines", 0) < old.get("pass_lines", 0):
        diffs.append(f"pass_lines: {old.get('pass_lines', 0)} -> {new['pass_lines']} (fewer verdicts reached)")
    if new.get("fail_lines", 0) > old.get("fail_lines", 0):
        diffs.append(f"fail_lines: {old.get('fail_lines', 0)} -> {new['fail_lines']}")
    if diffs:
        print("receipt compare: DRIFT against the reference")
        for d in diffs:
            print(f"    {d}")
        return 1
    print("receipt compare: no drift against the reference")
    return 0


COMPARED = ("gate", "verdict", "ok_lines", "pass_lines", "fail_lines", "legs",
            "tally_ok", "tally_fail", "hashes")


def promote(gate, src_dir="target/receipts", out_dir="tools/receipts_known_good"):
    """Keep a local receipt as the committed reference for this gate."""
    name = gate.replace(".sh", "") + ".json"
    src = os.path.join(src_dir, name)
    if not os.path.exists(src):
        print(f"gate_receipt: no local receipt at {src} -- run ./tools/run_gate.sh {gate} first")
        return 1
    full = json.load(open(src, encoding="utf-8"))
    if full.get("verdict") != "PASS":
        print(f"gate_receipt: refusing to promote a {full.get('verdict')} receipt")
        return 1
    # Only the diffable facts: a timestamp or a log digest would make the
    # committed file change on every run without telling anyone anything.
    trimmed = {k: full[k] for k in COMPARED if k in full}
    trimmed["recorded"] = time.strftime("%Y-%m-%d", time.gmtime())
    trimmed["note"] = ("known-good reference for tools/run_gate.sh; refresh with "
                       "gate_receipt.py promote, never by hand")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as out:
        json.dump(trimmed, out, indent=2, sort_keys=True)
        out.write("\n")
    print(f"gate_receipt: promoted {src} -> {path}")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "emit":
        gate = argv[1]
        log_path = None
        out_dir = "target/receipts"
        status = 0
        rest = argv[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--log":
                log_path = rest[i + 1]
                i += 2
            elif rest[i] == "--out":
                out_dir = rest[i + 1]
                i += 2
            elif rest[i] == "--status":
                status = int(rest[i + 1])
                i += 2
            else:
                print(f"unknown argument: {rest[i]}")
                return 2
        if not log_path or not os.path.exists(log_path):
            print(f"gate_receipt: no transcript at {log_path}")
            return 2
        emit(gate, log_path, out_dir, status)
        return 0
    if cmd == "compare":
        return compare(argv[1], argv[2])
    if cmd == "promote":
        gate = argv[1]
        src_dir = "target/receipts"
        out_dir = "tools/receipts_known_good"
        rest = argv[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--from":
                src_dir = rest[i + 1]
                i += 2
            elif rest[i] == "--out":
                out_dir = rest[i + 1]
                i += 2
            else:
                print(f"unknown argument: {rest[i]}")
                return 2
        return promote(gate, src_dir, out_dir)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
