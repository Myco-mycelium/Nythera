# The Nyrqis Live Demo — Presenter Guide

A complete, scripted operator session against a **real daemon on this
machine**: bring-up, containers, a **real signed repository** driving
the UPD-001 Option A update cycle (verify → restore point → apply →
audit → rollback → tamper refusal), the **CRY-001 Option A local crash
spool** driven through a real daemon restart, and the DBG-001 incident
bundle. No network egress anywhere; every act prints PASS/FAIL and the
script exits 0 only on a full pass.

## Quick start

```bash
cd source/nyhal-linux-backend
python3 demo/run_demo.sh            # the full session (~40 s)
python3 demo/run_demo.sh --quick    # Acts I + III + IV only
```

Expected tail:

```
DEMO VERDICT: 18/18 checks passed — DEMO PASS
```

## The five acts and what each proves

| Act | Surface | The property it demonstrates |
|---|---|---|
| I — bring-up | `ping` / `status` over the IPC transport | a real daemon answers on a private Unix-socket transport |
| II — containers | `containers list`, per-container audit refusal | the operator surface answers; refusals are well-formed (`rc=1` + reason), never transport noise |
| III — packages | `packages verify/update/rollback` + a tampered-delta attempt | the UPD-001 Option A wiring: **verify → restore point → apply → audit**, the manifest inventory updated, rollback restores the pre-apply bytes, and a **tampered delta is refused fail-closed with the content untouched** |
| IV — crash reporting | a real daemon **restart** with `--crash-spool` | the CRY-001 Option A path: §4.5 recovery detects the dead daemon and spools a **redacted** local report; `crash list/show` inspect it |
| V — diagnostics | `debug bundle` | the DBG-001 incident bundle composes existing ops with **redaction default-on** |

## What is real vs scripted

Everything is real: a real daemon process, real IPC, real Ed25519
signing (the publisher key and signed index are built live by Act III
using the shipped `package_signing`/`package_repo`/`delta_update`
machinery), real filesystem mutation on the applied delta, real
restore-point bytes verified, and a real restart driving the §4.5
recovery path. The only "scripted" part is the scenario order — the
script is the presenter's score, not a mock.

## Talking points

- **Fail-closed everywhere**: watch Act III's tamper refusal and Act
  III's rollback — the restore point is taken BEFORE any mutation, and
  the refusal leaves the install byte-identical.
- **No egress**: the crash spool and the update path both import no
  HTTP client (contract-pinned in `tests/test_crash_spool.py` and
  `tests/test_update_orchestrate.py`); Act IV's report never leaves
  the machine.
- **Redaction default-on**: Act IV's spooled report and Act V's bundle
  both carry the DBG-001 redaction marker.
- **The trust chain is short**: publisher key → signed index →
  verified entry → verified delta payload → applied. One corrupted
  byte anywhere breaks the chain and the demo shows exactly that.

## After the demo

The demo's temp directory is removed on exit. To inspect anything by
hand, re-run with a fixed directory or use the underlying commands
directly:

```bash
nyrqisctl crash list --spool-dir /var/lib/nyrqis/crash-spool
nyrqisctl packages verify --repo-root /srv/nypkg --trust-store trust.json
```

## Requirements

- Python 3.11+ with PyNaCl (signed-repo acts degrade to SKIP without
  it — the contract suites note the skip; the demo's Act III fails
  loudly instead, by design)
- No root, no network, no external services
