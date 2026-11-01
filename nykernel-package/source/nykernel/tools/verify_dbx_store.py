#!/usr/bin/env python3
"""
verify_dbx_store.py -- does an operator's dbx store ACTUALLY revoke this
certificate?

ADR-0029 fixes the composition side: the signing host embeds the target's
To-Be-Signed hash (or the whole certificate) into an authenticated update.
The other end of that pipeline is a fleet: a store on a machine, or a
varstore image collected from one. "We issued the revocation" and "this
machine will refuse the dead carrier" are different facts, and only the
second one is enforcement.

This tool reads the second fact:

    ./tools/verify_dbx_store.py --store VARS.fd --cert leaf.pem
    ./tools/verify_dbx_store.py --store a.fd --store b.fd --cert leaf.pem \
                                --absent replacement.pem
    ./tools/verify_dbx_store.py --fleet collected/ --cert leaf.pem
    ./tools/verify_dbx_store.py --dbx composed-list.der --cert leaf.pem

`--fleet DIR` audits a whole collected fleet in one call: every file in
DIR matching `--fleet-glob` (default `*.fd`) is a machine's store, and
the run ends with a per-machine summary line plus a count of how many
machines hold each kind of refusal, so a rollout can be reported as "412
of 480 enforcing, 68 with no dbx variable" instead of a wall of blocks.
The machine name is the store's file name, prefixed with the directory it
was collected into.

A `--store` is an edk2/OVMF variable store image (what `virt-fw-vars`
reads and writes); the dbx bytes come out of it via
`virt-fw-vars --output-json`, so a store is read through the same
tooling the gates compose with rather than a second, private parser. A
`--dbx` is the bare EFI_SIGNATURE_LIST sequence, or an auth2 update
package, which is what a vendor ships before it reaches a store.

Both enforced forms count (they are the two branches of the kernel's
walk, ADR-0029): `EFI_CERT_X509` by whole-certificate byte match, and
`EFI_CERT_X509_SHA256` by sha256 over the certificate's tbsCertificate
TLV. The TBS hash here is derived by this file's own DER walk -- not by
calling the repo helper the ceremony signs with -- so agreement with the
signing path is measured, not assumed.

What the verdict distinguishes, because each means something different
operationally:

  revoked, effective      an enforced entry exists and its revocation
                          date is not in the future: the machine refuses
                          the carrier now.
  revoked, not yet        the entry exists but its EFI_TIME is later than
                          `--at` (default: today). Firmware does not
                          revoke early, so this is REFUSED by default --
                          "we revoked it" would be false today. Pass
                          `--allow-pending` when filing a scheduled
                          revocation on purpose.
  present, not enforced   the target's hash appears under an entry type
                          this kernel does not read for revocation (an
                          EFI_CERT_SHA256 image-hash list, or SHA-1/224/
                          384/512 forms -- ADR-0030's reported-only
                          classes). REFUSED: the store carries it, the
                          machine ignores it.
  absent                  no enforced entry. REFUSED.

`--absent CERT` is the other direction, and the cheap way to catch an
over-broad revocation: assert a certificate (typically the replacement
leaf issued by the rotation) is NOT revoked in this store.

Exit 0 when every store satisfies every check, 1 when any check fails
(the failing fact is named), 2 on usage/IO errors.

Requires: virt-fw-vars on PATH (or in ~/.local/bin) for `--store`.
"""

import base64
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

# The GUID classes this tool has to name individually: the two the
# kernel's walk ENFORCES (ADR-0029), and the classes in which the same
# certificate can be "covered" without the machine ever reading it
# (ADR-0030's report-only set).
X509 = "a5c059a1-94e4-4aa7-87b5-ab155c2bf072"
X509_SHA256 = "3bd2a492-96c0-4079-b420-fcf98ef103ed"
# The classes in which a certificate's hash appears WITHOUT the kernel
# reading it as a revocation (ADR-0030): image-hash lists, and the
# SHA-384/512 To-Be-Signed forms. class -> (hashlib name, digest bytes).
UNENFORCED_HASH = {
    "c1c41626-504c-4092-aca9-41f936934328": ("sha256", 32),
    "826ca512-cf10-4ac9-b187-be01496631bd": ("sha1", 20),
    "0b6e5233-a65c-44c9-9407-d9ab83bfc8bd": ("sha224", 28),
    "ff3e5307-9fd0-48c9-85f1-8ad56c701e01": ("sha384", 48),
    "093e0fae-a6c4-4f50-9f1b-d41e2b89c19a": ("sha512", 64),
    "7076876e-80c2-4ee6-aafd-28c3499e3fdb": ("sha384", 48),
    "446dbf63-2502-4cda-bcfa-2465d2b0fe9d": ("sha512", 64),
}
# Entries are: SignatureOwner GUID (16) then the class payload. The two
# enforced forms are the ones the kernel's walk reads (ADR-0029).
ENFORCED_ENTRY = {X509_SHA256: 16 + 32 + 16}  # owner + tbs sha256 + EFI_TIME
# EFI_CERT_X509 entries are owner GUID + the DER certificate, whose length
# varies, so that form is compared rather than sized.


# --- DER ------------------------------------------------------------------
def read_tlv(buf, off):
    """(tag, content_start, total_length) for the DER TLV at `off`."""
    if off + 2 > len(buf):
        raise ValueError(f"truncated DER at +{off}")
    tag = buf[off]
    first = buf[off + 1]
    if first < 0x80:
        hdr_len, length = 2, first
    elif first == 0x80:
        raise ValueError(f"indefinite length at +{off} (not DER)")
    else:
        n = first & 0x7F
        if n > 4 or off + 2 + n > len(buf):
            raise ValueError(f"bad length field at +{off}")
        length = int.from_bytes(buf[off + 2:off + 2 + n], "big")
        hdr_len = 2 + n
    total = hdr_len + length
    if off + total > len(buf):
        raise ValueError(
            f"TLV at +{off} declares {total} bytes, {len(buf) - off} remain")
    return tag, off + hdr_len, total


def cert_tbs(buf):
    """tbsCertificate WHOLE TLV -- tag, length and content, which is what
    RFC 5280 4.1.1.2 hashes and what the kernel's dbx walk hashes.

    Deliberately a second implementation of a rule that already exists in
    tools/patch_carrier_negatives.py and uefi/src/trust.rs: the fleet-side
    check must be able to DISAGREE with the signing path, or it is only
    restating it. The tests cross-check the three against a hash read out
    of a real firmware-composed store.
    """
    tag, content, total = read_tlv(buf, 0)
    if tag != 0x30:
        raise ValueError("certificate is not a DER SEQUENCE")
    if total != len(buf):
        raise ValueError(f"{len(buf) - total} trailing bytes after the certificate")
    inner_tag, _, inner_total = read_tlv(buf, content)
    if inner_tag != 0x30:
        raise ValueError("first element of the certificate is not the tbsCertificate SEQUENCE")
    return bytes(buf[content:content + inner_total])


def load_cert(path):
    """DER bytes of a certificate file (PEM or DER)."""
    raw = open(path, "rb").read()
    if b"-----BEGIN CERTIFICATE-----" in raw:
        text = raw.decode("ascii", "replace")
        start = text.index("-----BEGIN CERTIFICATE-----") + len("-----BEGIN CERTIFICATE-----")
        end = text.index("-----END CERTIFICATE-----", start)
        body = "".join(text[start:end].split())
        try:
            return base64.b64decode(body, validate=True)
        except Exception as exc:
            raise ValueError(f"{path}: PEM body does not decode: {exc}")
    return raw


# --- EFI_TIME -------------------------------------------------------------
def efi_time(raw):
    """(date, raw) from a 16-byte EFI_TIME; date is None when the stamp is
    not a readable calendar date (the caller reports that, it does not
    silently treat it as "now")."""
    if len(raw) != 16:
        raise ValueError(f"EFI_TIME is {len(raw)} bytes, not 16")
    year, month, day = struct.unpack("<HBB", raw[:4])
    try:
        return datetime.date(year, month, day), raw
    except ValueError:
        return None, raw


def fmt_time(raw):
    d, _ = efi_time(raw)
    if d is None:
        year, month, day = struct.unpack("<HBB", raw[:4])
        return f"unreadable EFI_TIME {year:04d}-{month:02d}-{day:02d}"
    return d.isoformat()


# --- reading a store ------------------------------------------------------
IMAGE_SECURITY_DB = "d719b2cb-3d3a-4596-a3bc-dad00e67656f"


def virt_fw():
    path = shutil.which("virt-fw-vars")
    if path:
        return path
    fallback = os.path.expanduser("~/.local/bin/virt-fw-vars")
    return fallback if os.access(fallback, os.X_OK) else None


def dbx_from_store(path):
    """(dbx bytes, attributes) out of an edk2 variable store image."""
    tool = virt_fw()
    if not tool:
        raise RuntimeError(
            "virt-fw-vars not found (pip name: virt-firmware): a store cannot be "
            "read without it; pass --dbx to check a composed list instead")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "vars.json")
        proc = subprocess.run(
            [tool, "-i", path, "--output-json", out, "-l", "ERROR"],
            capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(out):
            raise ValueError(
                f"{path}: virt-fw-vars could not read the store "
                f"(exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:200]}")
        doc = json.load(open(out))
    for var in doc.get("variables", []):
        if var.get("name") == "dbx" and var.get("guid") == IMAGE_SECURITY_DB:
            try:
                return bytes.fromhex(var.get("data", "")), var.get("attr")
            except ValueError as exc:
                raise ValueError(f"{path}: dbx is not hex in the dump: {exc}")
    # No dbx is not a read error: it is the strongest possible "nothing is
    # revoked" fact, and the caller reports it as such.
    return None, None


# --- entries --------------------------------------------------------------
def entries(buf):
    """[(list_type_guid, entry_bytes)] validated by dbx_inventory.parse_lists
    (the parser measured against Microsoft's own artifacts), then sliced by
    the sizes it reports."""
    lists, end, _ = inv.parse_lists(buf, 0)
    if end != len(buf):
        raise ValueError(f"{len(buf) - end} bytes after the last signature list")
    if not lists:
        raise ValueError("no signature lists at all")
    out = []
    off = 0
    for li in lists:
        base = off + 28 + li["header_size"]
        for i in range(li["count"]):
            s = base + i * li["entry_size"]
            out.append((li["type"], bytes(buf[s:s + li["entry_size"]])))
        off += li["list_size"]
    return lists, out


def scan(lists, ents, der, tbs_bytes):
    """Classify every entry against the target certificate."""
    digests = {name: hashlib.new(name, tbs_bytes).digest()
               for name in ("sha1", "sha224", "sha256", "sha384", "sha512")}
    found, unenforced = [], []
    for typ, entry in ents:
        # EFI_CERT_X509: the entry is the owner GUID followed by the DER
        # certificate, so a byte match on the certificate alone is the
        # whole-certificate revocation ADR-0029's other branch reads.
        if typ == X509:
            if entry[16:] == der:
                found.append({"form": "EFI_CERT_X509", "enforced": True,
                              "stamp": None,
                              "detail": "whole-certificate byte match"})
            continue
        # EFI_CERT_X509_SHA256: owner GUID, then the 32-byte To-Be-Signed
        # hash, then the 16-byte revocation EFI_TIME. The hash starts at
        # +16, not +0 -- the owner GUID is not part of the identity.
        if typ == X509_SHA256 and len(entry) == ENFORCED_ENTRY[X509_SHA256]:
            if entry[16:48] == digests["sha256"]:
                stamp = entry[48:64]
                found.append({"form": "EFI_CERT_X509_SHA256", "enforced": True,
                              "stamp": stamp,
                              "detail": f"To-Be-Signed sha256, revoked {fmt_time(stamp)}"})
            continue
        spec = UNENFORCED_HASH.get(typ)
        if spec and len(entry) >= 16:
            hname, size = spec
            if entry[16:16 + size] == digests[hname]:
                unenforced.append({"form": inv.TYPES.get(typ, typ),
                                   "detail": f"{hname} of the certificate's "
                                             f"To-Be-Signed content"})
    return found, unenforced


def check(store, dbx_bytes, der, label, at, allow_pending, absent):
    """Returns (facts, failures). Facts are what was seen; a failure is a
    (category, message) pair -- the message is the named statement, the
    category is what makes a fleet summary answer "why" for 480 machines
    without a human reading 480 blocks."""
    if dbx_bytes is None:
        return (
            [f"store {store} has no dbx variable under the image-security-database "
             f"GUID {IMAGE_SECURITY_DB}"],
            [("no-dbx-variable",
              f"no enforced revocation entry for {label}: this store has no dbx "
              f"variable at all, so it revokes no certificate")])
    lists, ents = entries(dbx_bytes)
    tbs = cert_tbs(der)
    tbs_sha256 = hashlib.sha256(tbs).hexdigest()
    facts, failures = [], []
    facts.append(f"dbx {len(dbx_bytes)} bytes, {len(lists)} list(s), {len(ents)} entry(ies)")
    for li in lists:
        facts.append(f"  {li['name']:26} entry {li['entry_size']:4}B x {li['count']:4}")

    found, unenforced = scan(lists, ents, der, tbs)
    facts.append(f"target {label}: To-Be-Signed sha256 {tbs_sha256}")

    if not found:
        if unenforced:
            for u in unenforced:
                failures.append((
                    "ignored-class",
                    f"target {label} appears as {u['form']} ({u['detail']}) -- a class "
                    f"the kernel's revocation walk does not read (ADR-0030), so this "
                    f"store does NOT revoke it"))
        else:
            failures.append((
                "absent",
                f"no enforced revocation entry for {label} in this store: neither an "
                f"EFI_CERT_X509 whole-certificate match nor an EFI_CERT_X509_SHA256 "
                f"entry over this certificate's To-Be-Signed sha256"))
    else:
        for m in found:
            facts.append(f"  match: {m['form']} -- {m['detail']}")
        effective = []
        pending = []
        for m in found:
            if m["stamp"] is None:
                effective.append(m)
                continue
            when, _ = efi_time(m["stamp"])
            if when is None:
                failures.append((
                    "bad-stamp",
                    f"target {label}: an enforced {m['form']} entry carries an "
                    f"unreadable EFI_TIME ({fmt_time(m['stamp'])}) -- firmware's "
                    f"comparison against it is undefined, so this is not a usable "
                    f"revocation"))
            elif when <= at:
                effective.append(m)
            else:
                pending.append((when, m))
        for when, m in pending:
            if allow_pending:
                facts.append(f"  pending: {m['form']} becomes effective {when.isoformat()} "
                             f"(--allow-pending)")
            else:
                failures.append((
                    "scheduled",
                    f"target {label} is revoked only FROM {when.isoformat()} "
                    f"({m['form']}), which is later than --at {at.isoformat()}: firmware "
                    f"does not revoke early, so this store does not revoke it yet "
                    f"(pass --allow-pending if the schedule is intended)"))
        if effective:
            facts.append(f"  effective: {len(effective)} enforced entry(ies) revoke "
                         f"{label} at {at.isoformat()}")

    for path, ader, alabel in absent:
        afound, aunenforced = scan(lists, ents, ader, cert_tbs(ader))
        live = []
        for m in afound:
            if m["stamp"] is None:
                live.append((None, m))
            else:
                when, _ = efi_time(m["stamp"])
                if when is None or when <= at:
                    live.append((when, m))
                elif allow_pending:
                    facts.append(f"  absent-check {alabel}: scheduled from "
                                 f"{when.isoformat()} (--allow-pending)")
                else:
                    failures.append((
                        "absent-scheduled",
                        f"absent-check {alabel}: an enforced {m['form']} entry revokes it "
                        f"from {when.isoformat()}, later than --at {at.isoformat()} -- "
                        f"a scheduled revocation of a certificate that must stay valid "
                        f"(pass --allow-pending to accept the schedule)"))
        for when, m in live:
            failures.append((
                "over-broad",
                f"absent-check {alabel} FAILED: this store revokes it too "
                f"({m['form']}, {m['detail']}) -- a revocation that covers the "
                f"replacement certificate is an over-broad revocation"))
        if not afound and aunenforced:
            facts.append(f"  absent-check {alabel}: present only in a non-enforced class "
                         f"({aunenforced[0]['form']}), which does not revoke it")
    return facts, failures


def main(argv):
    stores, dbxs, certs, absents, fleets = [], [], [], [], []
    at = datetime.date.today()
    allow_pending = False
    as_json = False
    fleet_glob = "*.fd"
    args = list(argv[1:])
    while args:
        a = args.pop(0)
        if a == "--store":
            if not args:
                return usage("--store needs a path")
            stores.append(args.pop(0))
        elif a == "--fleet":
            if not args:
                return usage("--fleet needs a directory")
            fleets.append(args.pop(0))
        elif a == "--fleet-glob":
            if not args:
                return usage("--fleet-glob needs a pattern")
            fleet_glob = args.pop(0)
        elif a == "--dbx":
            if not args:
                return usage("--dbx needs a path")
            dbxs.append(args.pop(0))
        elif a == "--cert":
            if not args:
                return usage("--cert needs a path")
            certs.append(args.pop(0))
        elif a == "--absent":
            if not args:
                return usage("--absent needs a path")
            absents.append(args.pop(0))
        elif a == "--at":
            if not args:
                return usage("--at needs YYYY-MM-DD")
            try:
                at = datetime.date.fromisoformat(args.pop(0))
            except ValueError:
                return usage("--at must be YYYY-MM-DD")
        elif a == "--allow-pending":
            allow_pending = True
        elif a == "--json":
            as_json = True
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            return usage(f"unknown argument: {a}")

    if len(certs) != 1:
        return usage("exactly one --cert is required")

    # A fleet directory expands into one store per machine, sorted so two
    # runs over the same collection report in the same order. An empty (or
    # unreadable) directory is refused rather than reported as "0 failures":
    # auditing nothing and auditing everything successfully are not the same
    # verdict, and a summary that conflates them is worse than no summary.
    groups = {}
    for d in fleets:
        if not os.path.isdir(d):
            return usage(f"--fleet {d}: not a directory")
        found = sorted(f for f in os.listdir(d)
                       if fnmatch.fnmatch(f, fleet_glob)
                       and os.path.isfile(os.path.join(d, f)))
        if not found:
            return usage(f"--fleet {d}: no files match '{fleet_glob}' -- auditing an "
                         f"empty fleet proves nothing")
        for f in found:
            p = os.path.join(d, f)
            groups[p] = {"group": d, "machine": f}
            stores.append(p)
    targets = stores + dbxs
    if not targets:
        return usage("one or more --store, --fleet or --dbx paths are required")

    try:
        der = load_cert(certs[0])
        label = cert_label(certs[0], der)
        absent_specs = [(p, load_cert(p), cert_label(p, load_cert(p))) for p in absents]
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 2

    results = []
    rc = 0
    for path in targets:
        where = groups.get(path, {})
        try:
            if path in stores:
                dbx_bytes, attr = dbx_from_store(path)
                facts, failures = check(path, dbx_bytes, der, label, at,
                                        allow_pending, absent_specs)
                facts.insert(0, f"store {path} (dbx attrs 0x{(attr or 0):x})")
            else:
                doc = inv.load(path)
                raw = open(path, "rb").read()
                if doc["form"] == "auth2":
                    # The stored variable is the payload after the descriptor.
                    dbx_bytes = raw[16 + doc["win_cert_length"]:]
                else:
                    dbx_bytes = raw
                facts, failures = check(path, dbx_bytes, der, label, at,
                                        allow_pending, absent_specs)
                facts.insert(0, f"package {path} (form {doc['form']}"
                                + (f", stamp {doc['stamp']}" if doc["stamp"] else "") + ")")
        except (OSError, ValueError, RuntimeError, struct.error) as exc:
            results.append({"path": path, "ok": False, "facts": [],
                            "failures": [{"category": "unreadable",
                                          "message": f"cannot read {path}: {exc}"}],
                            **where})
            rc = 1
            continue
        ok = not failures
        rc = rc or (0 if ok else 1)
        results.append({"path": path, "ok": ok, "facts": facts,
                        "failures": [{"category": c, "message": m}
                                     for c, m in failures], **where})

    if as_json:
        print(json.dumps({"target": certs[0], "label": label,
                          "at": at.isoformat(), "results": results}, indent=1))
        return rc

    for r in results:
        print(f"== {r['path']}")
        for f in r["facts"]:
            print(f"   {f}" if not f.startswith("  ") else f"   {f}")
        for f in r["failures"]:
            print(f"   FAIL: {f['message']}")

    # The fleet view: one line per machine, then how many machines hold
    # each KIND of refusal. A rollout report needs the distribution, not
    # 480 blocks; the blocks above stay for whoever has to fix one.
    if fleets:
        print()
        print(f"== fleet summary: {len(fleets)} collection(s), "
              f"{len(results)} machine(s), target {label}")
        for r in results:
            name = r["machine"] if "machine" in r else r["path"]
            if r["ok"]:
                line = "enforcing"
                for f in r["facts"]:
                    if "match: " in f:
                        line = "enforcing (" + f.split("match: ")[1].split(" -- ")[0] + ")"
                        break
                print(f"   PASS  {name:32} {line}")
            else:
                first = r["failures"][0]
                print(f"   FAIL  {name:32} {first['category']}: "
                      f"{shorten(first['message'], 96)}")
        counts = {}
        for r in results:
            if r["ok"]:
                counts["enforcing"] = counts.get("enforcing", 0) + 1
            else:
                for c in {f["category"] for f in r["failures"]}:
                    counts[c] = counts.get(c, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"   {len(results)} machine(s): "
              + ", ".join(f"{n} {name}" for name, n in ranked))

    bad = [r for r in results if not r["ok"]]
    if bad:
        print(f"FAIL: {len(bad)} of {len(results)} artifact(s) do not revoke {label} "
              f"as required (each fact above is named)")
        return 1
    print(f"PASS: {len(results)} artifact(s) revoke {label} through an enforced dbx "
          f"form, effective at {at.isoformat()}"
          + (f", and do not revoke the {len(absents)} certificate(s) named --absent"
             if absents else ""))
    return 0


def shorten(text, limit):
    """Cut at a word boundary -- a summary line that ends mid-word reads as
    a corrupted record rather than a truncated one."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]) + " .."


def cert_label(path, der):
    """CN and serial, read with openssl when available; the DER sha256
    always, so the label is unambiguous even without it."""
    cn = None
    s = shutil.which("openssl")
    if s:
        try:
            out = subprocess.run([s, "x509", "-inform", "DER", "-noout",
                                  "-subject", "-serial", "-outform", "PEM"],
                                 input=der, capture_output=True, check=True).stdout
            text = out.decode("utf-8", "replace")
            for line in text.splitlines():
                if line.startswith("subject="):
                    cn = line[len("subject="):].strip()
                elif line.startswith("serial="):
                    cn = f"{cn or path} (serial {line[len('serial='):]})"
        except subprocess.CalledProcessError:
            pass
    return cn or f"{path} (der sha256 {hashlib.sha256(der).hexdigest()[:16]}..)"


def usage(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    print(__doc__.strip().split("Usage:")[0].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
