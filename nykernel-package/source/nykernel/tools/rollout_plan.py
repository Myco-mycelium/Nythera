#!/usr/bin/env python3
"""
rollout_plan.py -- given a signed dbx revocation package and a collected
fleet, produce the per-machine DELIVERY plan: which machines need it,
which can take it at all, and from what date each one will enforce.

ADR-0029 draws a boundary at "delivery and rollout scheduling are platform
process". That is true, and it is also where the last unmeasured claim
lived: once a package is composed and verified, nobody could answer "will
this land on machine N?" without trying it on machine N. This tool answers
it from the store images an operator already collects, and it does not
deliver, schedule or touch anything:

    ./tools/rollout_plan.py --package uefi/dist/dbx-update-ceremony/dbx-update.auth2 \\
        --cert dead-leaf.pem --kek-cert kek.pem --fleet collected/ \\
        [--canary machine-01.fd] [--absent replacement.pem] [--out plan/]

Four facts decide each machine, and each is a way a rollout breaks:

  1. STATE      already enforced at the package's stamp? Then the machine
                needs nothing: SKIP. (The state comes from
                tools/verify_dbx_store.py's own verdicts, so the plan and
                the post-delivery audit cannot disagree about what
                "enforcing" means.)
  2. KEK        the package's PKCS#7 is verified against --kek-cert here,
                and that certificate must appear in THIS store's KEK
                variable. A package signed by a key the store does not
                carry is refused by firmware at SetVariable time -- on the
                machine, mid-rollout. BLOCKED: kek-mismatch.
  3. STAMP      firmware rejects a non-increasing EFI_TIME, so the
                package's stamp must be strictly newer than this store's
                dbx timestamp (no dbx variable = nothing to advance past).
                BLOCKED: stale-stamp (the package is already spent there,
                and a replay would be indistinguishable).
  4. REPLACEMENT  an EFI_VARIABLE_AUTHENTICATION_2 write REPLACES the dbx
                variable; it does not append. A package carrying fewer
                entries than the store's dbx silently un-revokes the
                difference -- which is why vendor dbx updates ship the
                complete list (the 443-entry amd64 artifact ADR-0030
                measured). The revocation ceremony composes exactly one
                entry, by its own scope check, so any store already
                carrying other entries is BLOCKED:
                would-drop-existing-entries, naming the classes it would
                drop. The remedy is a superset list re-signed for that
                class of store, not a flag on this tool.

                       -- READS, NEVER WRITES --

The tool opens the package and each store image read-only, prints the
plan, and (with --out) writes rollout-plan.txt plus rollout-plan.json. The
JSON is deterministic for a given --at and carries a plan sha256, so the
plan an operator approved is the plan that gets executed.

Waves: --canary names machines from the fleet. They form wave 1, the rest
of the applicable machines wave 2, and the plan prints the audit command
that has to pass after each wave. A canary that is not in the fleet
refuses the plan (a canary nobody delivers to proves nothing), and so does
a canary that is already enforcing or blocked (a rollout cannot be
canaried on a machine the package does not reach).

Exit 0 when every machine is apply or skip, 1 when any machine is blocked
(the reason is named) or the plan's own preconditions fail, 2 on usage or
IO errors.

Requires: virt-fw-vars (pip name: virt-firmware) for --fleet, openssl for
the package's signature.
"""

import datetime
import fnmatch
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbx_inventory as inv  # noqa: E402  (sibling module, same tools dir)
import verify_dbx_store as vbs  # noqa: E402  (the store reader + verdicts)

# EFI_GLOBAL_VARIABLE, the GUID KEK/PK are kept under.
PK_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
IMAGE_SECURITY_DB = vbs.IMAGE_SECURITY_DB


# --- the store -------------------------------------------------------------
def read_vars(path):
    """{(name, guid): {data, time}} for an edk2 variable store image.

    Read through `virt-fw-vars --output-json`, the same tooling the gates
    compose with, so this plan and the gates cannot drift into two ideas of
    what a store contains.
    """
    tool = vbs.virt_fw()
    if not tool:
        raise RuntimeError(
            "virt-fw-vars not found (pip name: virt-firmware): a machine's "
            "store cannot be read without it")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "vars.json")
        proc = subprocess.run(
            [tool, "-i", path, "--output-json", out, "-l", "ERROR"],
            capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(out):
            raise ValueError(
                f"virt-fw-vars could not read the store (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:200]}")
        doc = json.load(open(out))
    vars_ = {}
    for var in doc.get("variables", []):
        try:
            data = bytes.fromhex(var.get("data", ""))
        except ValueError:
            data = b""
        when = None
        if var.get("time"):
            try:
                when = bytes.fromhex(var["time"])
            except ValueError:
                when = None
        vars_[(var.get("name"), var.get("guid"))] = {
            "data": data, "time": when, "attr": var.get("attr")}
    return vars_


def store_dbx(path):
    """(dbx bytes or None, the dbx variable's EFI_TIME or None)."""
    var = read_vars(path).get(("dbx", IMAGE_SECURITY_DB))
    if var is None:
        return None, None
    return var["data"], var["time"]


def store_kek(path):
    var = read_vars(path).get(("KEK", PK_GUID))
    return None if var is None else var["data"]


def sig_entries(blob):
    """[(list type, entry bytes)] -- the same slice the verifier's walk uses."""
    if not blob:
        return []
    return vbs.entries(blob)[1]


# --- EFI_TIME --------------------------------------------------------------
def time_tuple(raw):
    """(year, month, day, hour, minute, second) or None when unreadable.

    Firmware compares the whole 16-byte structure field by field, so a
    date-only comparison would accept a package edk2 refuses (a same-day,
    earlier-time stamp). The tuple is the comparable part.
    """
    if raw is None or len(raw) != 16:
        return None
    year, month, day, hour, minute, second, _pad = struct.unpack("<HBBBBBB", raw[:8])
    return (year, month, day, hour, minute, second)


def fmt_time(raw):
    t = time_tuple(raw)
    if t is None:
        return "an unreadable EFI_TIME"
    return "%04d-%02d-%02d %02d:%02d:%02d" % t


# --- the package -----------------------------------------------------------
def read_package(path):
    """The package's own facts: stamp, payload, signature.

    A bare signature list is REFUSED rather than planned: it carries no
    EFI_TIME, so firmware cannot tell it from a replay and the stamp checks
    below would have nothing to compare.
    """
    raw = open(path, "rb").read()
    doc = inv.load(path)
    if doc["form"] != "auth2":
        raise ValueError(
            "the plan needs an EFI_VARIABLE_AUTHENTICATION_2 package: a bare "
            "signature list carries no EFI_TIME, so firmware cannot "
            "distinguish it from a replay")
    dw_len = doc["win_cert_length"]
    if len(raw) < 16 + dw_len:
        raise ValueError(f"truncated auth2 package: win-certificate length "
                         f"{dw_len} does not fit {len(raw)} bytes")
    stamp = raw[:16]
    p7 = raw[40:16 + dw_len]
    payload = raw[16 + dw_len:]
    if not p7:
        raise ValueError("the package carries no PKCS#7 signature")
    if not payload:
        raise ValueError("the package carries no signature list")
    return {
        "path": path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "stamp_raw": stamp,
        "stamp_date": datetime.date(*time_tuple(stamp)[:3]),
        "payload": payload,
        "p7": p7,
    }


def signing_content(package):
    """The bytes edk2 signs: name || GUID || attrs || EFI_TIME || payload.

    Reconstructed here so the signature check is over what firmware checks
    (UEFI 2.10 8.2.2, edk2 VerifyTimeBasedPayload). A package built for
    another variable, or with another attribute set, does not verify --
    which is the point: a plan over a package firmware will always refuse
    is not a plan.
    """
    import uuid
    return ("dbx".encode("utf-16-le")
            + uuid.UUID(IMAGE_SECURITY_DB).bytes_le
            + (0x1 | 0x2 | 0x4 | 0x20).to_bytes(4, "little")
            + package["stamp_raw"]
            + package["payload"])


# PKCS#7 ContentInfo wrapping, so `openssl cms` can read the descriptor's
# BARE SignedData. The package carries the bare form because that is what
# edk2 reads (ADR-0029's layout note); openssl's cms subcommand only parses
# the ContentInfo form, so it is re-wrapped here rather than assumed.
SIGNED_DATA_OID = bytes.fromhex("06092a864886f70d010702")


def _der_len(n):
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def wrap_signed_data(signed_data):
    """ContentInfo { OID signedData, [0] SignedData } around a bare SignedData."""
    if not signed_data or signed_data[0] != 0x30:
        raise ValueError("the descriptor's signature is not a DER SEQUENCE")
    inner = SIGNED_DATA_OID + b"\xa0" + _der_len(len(signed_data)) + signed_data
    return b"\x30" + _der_len(len(inner)) + inner


def verify_signer(package, kek_der):
    """Verify the package's PKCS#7 against kek_der; return the signer DER.

    The plan's KEK check is only meaningful if "the signer" is measured,
    not asserted: --kek-cert is compared to the certificate the package
    actually carries, so an operator cannot plan a rollout naming one key
    and carrying a package signed by another.
    """
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl not found: the package's signature cannot be "
                           "verified, and a plan over an unverified package is a "
                           "plan over an unchecked artifact")
    with tempfile.TemporaryDirectory() as tmp:
        p7 = os.path.join(tmp, "p7.der")
        content = os.path.join(tmp, "content.bin")
        signer_pem = os.path.join(tmp, "signer.pem")
        wrapped = wrap_signed_data(package["p7"])
        open(p7, "wb").write(wrapped)
        open(content, "wb").write(signing_content(package))
        proc = subprocess.run(
            [openssl, "cms", "-verify", "-binary", "-inform", "DER",
             "-in", p7, "-content", content, "-noverify", "-out", os.devnull],
            capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            raise ValueError(
                "the package's PKCS#7 does not verify against --kek-cert: "
                + (detail[-1][:200] if detail else "openssl refused it"))
        proc = subprocess.run(
            [openssl, "pkcs7", "-inform", "DER", "-in", p7, "-print_certs",
             "-out", signer_pem], capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.getsize(signer_pem):
            raise ValueError("the package carries no signer certificate")
        proc = subprocess.run(
            [openssl, "x509", "-in", signer_pem, "-outform", "DER"],
            capture_output=True)
        if proc.returncode != 0:
            raise ValueError("the package's signer certificate does not decode")
        signer_der = proc.stdout
    if signer_der != kek_der:
        raise ValueError(
            "the package's signer certificate is not --kek-cert (package signer "
            f"sha256 {hashlib.sha256(signer_der).hexdigest()[:16]}.., --kek-cert "
            f"{hashlib.sha256(kek_der).hexdigest()[:16]}..): name the key the "
            "package was signed with, or re-sign it with the KEK the fleet "
            "carries")
    return signer_der


# --- per-machine verdict ---------------------------------------------------
def machine_verdict(path, der, label, package, signer_der):
    """One machine's action: apply, skip, or blocked:<category>.

    STATE first: a machine that already enforces is not written to, so the
    preconditions of a write do not apply to it.
    """
    try:
        dbx, dbx_time = store_dbx(path)
        facts, failures = vbs.check(path, dbx, der, label,
                                    package["stamp_date"], False, [])
    except (OSError, ValueError, RuntimeError, struct.error) as exc:
        return {"action": "blocked", "category": "unreadable",
                "reason": f"cannot read the store: {exc}", "facts": []}

    # 1. does it already enforce the revocation at the package's stamp?
    if not failures:
        form = "an enforced dbx form"
        for f in facts:
            if "match: " in f:
                form = f.split("match: ")[1].split(" -- ")[0]
                break
        return {"action": "skip", "category": "already-enforcing",
                "reason": f"already revokes the target through {form} at "
                          f"{package['stamp_date'].isoformat()} -- no write needed",
                "facts": []}

    facts = (["no dbx variable: nothing to advance past, nothing to lose"]
             if dbx is None else [])

    # 2. will this store trust the signer?
    try:
        kek = store_kek(path)
        kek_entries = sig_entries(kek)
    except (OSError, ValueError, RuntimeError, struct.error) as exc:
        return {"action": "blocked", "category": "unreadable",
                "reason": f"cannot read the store's KEK: {exc}", "facts": facts}
    if kek is None:
        return {"action": "blocked", "category": "no-kek-variable",
                "reason": f"no KEK variable under {PK_GUID}: firmware cannot "
                          f"verify the package's PKCS#7, so the write is refused",
                "facts": facts}
    if not any(typ == vbs.X509 and entry[16:] == signer_der
               for typ, entry in kek_entries):
        return {"action": "blocked", "category": "kek-mismatch",
                "reason": f"this store's KEK does not carry the package's signer "
                          f"(sha256 {hashlib.sha256(signer_der).hexdigest()[:16]}..): "
                          f"firmware refuses an update signed by a key it does "
                          f"not trust at SetVariable time",
                "facts": facts}
    facts.append("KEK carries the signer")

    # 3. will firmware accept the stamp?
    if dbx_time is not None:
        old, new = time_tuple(dbx_time), time_tuple(package["stamp_raw"])
        if old is None:
            return {"action": "blocked", "category": "bad-store-stamp",
                    "reason": f"the store's dbx EFI_TIME ({dbx_time.hex()}) is not "
                              f"a readable calendar time, so firmware's comparison "
                              f"against it is undefined",
                    "facts": facts}
        if new <= old:
            return {"action": "blocked", "category": "stale-stamp",
                    "reason": f"the package's stamp "
                              f"{fmt_time(package['stamp_raw'])} is not newer than "
                              f"this store's dbx timestamp {fmt_time(dbx_time)}: "
                              f"firmware rejects a non-increasing time-based stamp, "
                              f"so this package is already spent here (or would be "
                              f"a replay)",
                    "facts": facts}
        facts.append(f"stamp advances the store's dbx timestamp "
                     f"{fmt_time(dbx_time)}")

    # 4. would the write un-revoke anything? (the variable is REPLACED)
    pkg_entries = set(sig_entries(package["payload"]))
    try:
        have_entries = sig_entries(dbx)
    except (ValueError, struct.error) as exc:
        return {"action": "blocked", "category": "unreadable",
                "reason": f"the store's dbx does not parse: {exc}", "facts": facts}
    lost = [e for e in have_entries if e not in pkg_entries]
    if lost:
        kinds = {}
        for typ, _ in lost:
            name = inv.TYPES.get(typ, typ)
            kinds[name] = kinds.get(name, 0) + 1
        detail = ", ".join(f"{n} {name}" for name, n in sorted(kinds.items()))
        return {"action": "blocked", "category": "would-drop-existing-entries",
                "reason": f"the package carries {len(pkg_entries)} entrie(s) and a "
                          f"dbx update REPLACES the variable; this store carries "
                          f"{len(lost)} entrie(s) the package does not ({detail}), "
                          f"which the write would un-revoke -- compose a superset "
                          f"list over this store's entries and re-sign it (the "
                          f"ceremony composes exactly one entry, by its own scope "
                          f"check)",
                "facts": facts}

    if not have_entries:
        facts.append("the store's dbx is empty: this package is the whole new value")
    else:
        facts.append(f"the store's {len(have_entries)} existing entrie(s) are all "
                     f"carried by the package (a non-destructive write)")
    return {"action": "apply", "category": "needs-package",
            "reason": f"revokes the target from "
                      f"{package['stamp_date'].isoformat()} and the package can "
                      f"land here",
            "facts": facts}


# --- plan ------------------------------------------------------------------
def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(package, der, cert_path, label, machines, canaries, at):
    results = []
    for machine, path in machines:
        v = machine_verdict(path, der, label, package, package["signer_der"])
        results.append({"machine": machine, "path": path,
                        "sha256": file_sha256(path), **v})

    by_name = {r["machine"]: r for r in results}
    applies = [r for r in results if r["action"] == "apply"]
    blocked = [r for r in results if r["action"] == "blocked"]

    problems = []
    if not results:
        problems.append("no machines: a plan over an empty fleet is not a plan")
    for name in canaries:
        if name not in by_name:
            problems.append(
                f"--canary {name}: no machine by that name in the fleet -- a "
                f"canary nobody delivers to proves nothing")
        elif by_name[name]["action"] == "skip":
            problems.append(
                f"--canary {name}: already enforces the revocation, so it cannot "
                f"canary a rollout it does not receive")
        elif by_name[name]["action"] == "blocked":
            problems.append(
                f"--canary {name}: blocked ({by_name[name]['category']}), and a "
                f"canary is the machine a rollout is judged on")

    waves = []
    if not problems:
        canary_results = [by_name[n] for n in canaries]
        rest = [r for r in applies if r["machine"] not in set(canaries)]
        if canary_results:
            waves.append({"wave": 1, "role": "canary",
                          "machines": [r["machine"] for r in canary_results]})
        if rest:
            waves.append({"wave": len(waves) + 1, "role": "fleet",
                          "machines": [r["machine"] for r in rest]})

    doc = {
        "package": {"path": package["path"], "bytes": package["bytes"],
                    "sha256": package["sha256"],
                    "stamp": package["stamp_date"].isoformat()},
        "target": {"path": cert_path, "label": label},
        "signer": {"path": package["kek_path"],
                   "sha256": hashlib.sha256(package["signer_der"]).hexdigest()},
        "at": at.isoformat(),
        "enforcement_effective": package["stamp_date"].isoformat(),
        "machines": sorted(results, key=lambda r: (r["action"] != "apply",
                                                   r["machine"])),
        "waves": waves,
        "problems": problems,
        "verdict": "executable" if not blocked and not problems else "not-executable",
        "summary": {
            "apply": len(applies),
            "skip": sum(1 for r in results if r["action"] == "skip"),
            "blocked": len(blocked),
            "blocked_categories": sorted({r["category"] for r in blocked}),
        },
    }
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    doc["plan_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return doc


def render(doc):
    pkg, target = doc["package"], doc["target"]
    out = [f"== rollout plan: {len(doc['machines'])} machine(s), target {target['label']}",
           f"   package  : {pkg['path']} ({pkg['bytes']} bytes, "
           f"sha256 {pkg['sha256'][:16]}..)",
           f"   signer   : {doc['signer']['path']} (sha256 "
           f"{doc['signer']['sha256'][:16]}..) -- the package's PKCS#7 verifies "
           f"against it",
           f"   stamp    : {pkg['stamp']} -- firmware starts refusing FROM this "
           f"date, never earlier",
           f"   prepared : {doc['at']} (--at; the plan claims no enforcement "
           f"before the stamp)"]
    for w in doc["waves"]:
        role = ("canary wave -- verify before the rest" if w["role"] == "canary"
                else "fleet")
        out.append(f"   wave {w['wave']} ({role}): " + ", ".join(w["machines"]))
    for r in doc["machines"]:
        if r["action"] == "blocked":
            head = f"   blocked {r['machine']:28} {r['category']}: {r['reason']}"
        else:
            head = f"   {r['action']:7} {r['machine']:28} {r['reason']}"
        out.append(head)
        for f in r["facts"]:
            out.append(f"           {f}")
    parts = [f"{doc['summary']['apply']} apply" if doc["summary"]["apply"] else "",
             f"{doc['summary']['skip']} already-enforcing" if doc["summary"]["skip"] else "",
             f"{doc['summary']['blocked']} blocked" if doc["summary"]["blocked"] else ""]
    summary = ", ".join(p for p in parts if p)
    if doc["summary"]["blocked_categories"]:
        summary += f" ({', '.join(doc['summary']['blocked_categories'])})"
    out.append(f"   {len(doc['machines'])} machine(s): {summary}")
    if doc["waves"]:
        out.append(f"   after each wave: ./tools/verify_dbx_store.py --fleet "
                   f"<collected/> --cert {target['path']} --at {pkg['stamp']}")
    out.append(f"   plan sha256: {doc['plan_sha256']}")
    return "\n".join(out)


def usage(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    return 2


def main(argv):
    package_path = cert_path = kek_path = fleet = None
    fleet_glob = "*.fd"
    absents, canaries = [], []
    at = datetime.date.today()
    out_dir = None
    as_json = False
    args = list(argv[1:])
    while args:
        a = args.pop(0)
        if a in ("--package", "--cert", "--kek-cert", "--fleet",
                 "--fleet-glob", "--absent", "--canary", "--at", "--out"):
            if not args:
                return usage(f"{a} needs a value")
            v = args.pop(0)
            if a == "--package":
                package_path = v
            elif a == "--cert":
                cert_path = v
            elif a == "--kek-cert":
                kek_path = v
            elif a == "--fleet":
                fleet = v
            elif a == "--fleet-glob":
                fleet_glob = v
            elif a == "--absent":
                absents.append(v)
            elif a == "--canary":
                canaries.append(v)
            elif a == "--out":
                out_dir = v
            else:  # --at
                try:
                    at = datetime.date.fromisoformat(v)
                except ValueError:
                    return usage("--at must be YYYY-MM-DD")
        elif a == "--json":
            as_json = True
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            return usage(f"unknown argument: {a}")

    missing = [n for n, v in (("--package", package_path), ("--cert", cert_path),
                              ("--kek-cert", kek_path), ("--fleet", fleet)) if not v]
    if missing:
        return usage(f"{', '.join(missing)} required (a plan needs an artifact, a "
                     f"target, the signer and the machines)")

    try:
        der = vbs.load_cert(cert_path)
        label = vbs.cert_label(cert_path, der)
        absent_specs = [(p, vbs.load_cert(p), vbs.cert_label(p, vbs.load_cert(p)))
                        for p in absents]
        kek_der = vbs.load_cert(kek_path)
        package = read_package(package_path)
        package["kek_path"] = kek_path
        package["signer_der"] = verify_signer(package, kek_der)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    # The package must first do the job it is being rolled out for, and not
    # more: a package that does not revoke the target (wrong artifact) or
    # that revokes a certificate named --absent (over-broad) has no plan.
    try:
        _, pkg_failures = vbs.check(package_path, package["payload"], der,
                                    label, package["stamp_date"], False,
                                    absent_specs)
    except (ValueError, struct.error) as exc:
        print(f"FAIL: the package's payload does not parse: {exc}", file=sys.stderr)
        return 1
    if pkg_failures:
        for _, message in pkg_failures:
            print(f"FAIL: package precondition: {message}", file=sys.stderr)
        return 1

    if not os.path.isdir(fleet):
        return usage(f"--fleet {fleet}: not a directory")
    names = sorted(f for f in os.listdir(fleet)
                   if fnmatch.fnmatch(f, fleet_glob)
                   and os.path.isfile(os.path.join(fleet, f)))
    if not names:
        return usage(f"--fleet {fleet}: no files match '{fleet_glob}' -- a plan "
                     f"over an empty fleet is not a plan")
    machines = [(f, os.path.join(fleet, f)) for f in names]

    doc = build_plan(package, der, cert_path, label, machines, canaries, at)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "rollout-plan.json"), "w") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        with open(os.path.join(out_dir, "rollout-plan.txt"), "w") as fh:
            fh.write(render(doc) + "\n")
            for p in doc["problems"]:
                fh.write(f"FAIL: {p}\n")
            if doc["verdict"] != "executable":
                fh.write(f"FAIL: {doc['summary']['blocked']} machine(s) cannot "
                         f"take this package as it stands\n")

    if as_json:
        print(json.dumps(doc, indent=1, sort_keys=True))
    else:
        print(render(doc))

    for p in doc["problems"]:
        print(f"FAIL: {p}", file=sys.stderr)

    if doc["problems"]:
        return 1
    if doc["summary"]["blocked"]:
        print(f"FAIL: {doc['summary']['blocked']} of {len(doc['machines'])} "
              f"machine(s) cannot take this package as it stands (each reason "
              f"above is named)", file=sys.stderr)
        return 1
    print(f"PASS: plan is executable on all {len(doc['machines'])} machine(s) -- "
          f"{doc['summary']['apply']} need the package, {doc['summary']['skip']} "
          f"already enforce it; nothing was delivered or scheduled (ADR-0029)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
