# Revoke a Secure Boot Carrier

This guide takes a certificate that must stop working — a compromised
carrier, or the dead leaf of a rotation — and gets you from "it is still
trusted" to "these machines refuse it", including the check that proves
the second half actually happened.

It is the operational half of ADR-0029. The design decisions (why the
hash-typed `EFI_CERT_X509_SHA256` form, why the revocation date gates
enforcement) are recorded there; this page is the sequence of commands,
the refusals you can hit, and what each one means.

**Who this is for:** whoever holds the KEK private key. If you do not
hold it, you cannot do this, and no amount of tooling changes that.

All commands below run from `nykernel-package/source/nykernel/`, where
the `tools/` scripts live.

## Before you start

| You need | Why |
|---|---|
| The certificate to revoke (PEM or DER) | The ceremony hashes *this* certificate; it does not look anything up |
| The KEK private key, mode 600 | The ceremony refuses group/world-readable keys |
| The **enrolled** KEK certificate | The certificate the target store actually carries — pass it explicitly (see step 2) |
| The anchor certificate | So the ceremony can refuse to revoke the trust anchor itself |
| A built tree | `./build.sh` generates the dev chain and the carrier; `sbvarsign`, `virt-fw-vars`, `openssl` and `python3` must be present |

If you are also changing keys — not just revoking one — run
`tools/rotate_leaf_ceremony.sh` (in
`nykernel-package/source/nykernel/`) first: it issues the replacement leaf and composes the dead leaf's
revocation together, with its own scope checks (the replacement must not
reuse the dead leaf's serial or To-Be-Signed hash). This guide assumes
the replacement exists or is not needed.

## 1. Name the target, and check it is not the anchor

There is no separate "check" command: the ceremony refuses. Revoking the
anchor would refuse **every** carrier, including the replacement you are
rotating to, so it is a named `FAIL` rather than a warning:

```
FAIL: the revocation target IS the trust anchor (<cert>)
```

If you see that, you named the wrong file — the anchor is the root that
signs your carriers, and revocation is for what it signs.

## 2. Run the ceremony

```
./tools/sign_dbx_update_ceremony.sh \
    --revoke path/to/dead-leaf.pem \
    --kek-cert path/to/kek.pem \
    --kek-key  path/to/kek-key.pem \
    --enrolled-kek path/to/enrolled-kek.pem \
    --anchor path/to/anchor.pem \
    --previous-stamp 2026-01-01 \
    --out /secure/revocation-2026-11
```

`--enrolled-kek` is the flag that matters and the one that is easy to get
wrong. Its **default is `--kek-cert`**, which is right for the dev chain
and wrong for production: naming the signer as its own reference makes the
"is this the KEK the store trusts?" check a tautology. Point it at the
certificate the target store actually carries — read it out of a real
store (`virt-fw-vars -i VARS.fd -p | grep KEK`) rather than trusting a
filename.

`--previous-stamp` is the previous package's `EFI_TIME`. Firmware refuses a
non-increasing time-based stamp, so a stale or replayed package is dead on
arrival; passing the previous stamp makes the ceremony refuse to build one.

On success:

```
PASS: signing/distribution ceremony complete
  package        : /secure/revocation-2026-11/dbx-update.auth2 (1478 bytes, sha256 37d68a4425557b37..)
  revoking       : CN = Nyrqis dev carrier leaf, OU = Nyrqis bringup (serial 13452F6F..)
  signer         : CN = Nyrqis dev trust anchor, OU = Nyrqis bringup (enrolled KEK match verified byte-for-byte)
  stamp          : 2027-01-01
  custody        : key mode 600 (no group/world bits)
  manifest       : /secure/revocation-2026-11/update-manifest.txt
  NOT done here  : delivery and rollout scheduling (platform process, ADR-0029)
```

## 3. Read the manifest and carry it with the package

`update-manifest.txt` records the package's sha256, the revoked
certificate's subject, serial and To-Be-Signed hash, the stamp, the signer,
and which certificate was used as the enrolled KEK. It is what a second
person checks the package against; the tool writes nothing into the
enrolled chain, so the manifest is the only record that this revocation
was composed deliberately.

## 4. Check the artifact before it goes anywhere

```
./tools/verify_dbx_store.py --dbx /secure/revocation-2026-11/dbx-update.auth2 \
    --cert path/to/dead-leaf.pem
```

This is the same verifier you will point at fleet stores in step 7, run on
the artifact itself: it parses the signature list, derives the
certificate's To-Be-Signed hash with its **own** DER walk, and compares.

If today is earlier than the stamp, it refuses:

```
FAIL: ... is revoked only FROM 2027-01-01 (EFI_CERT_X509_SHA256), which is later
      than --at 2026-11-01: firmware does not revoke early, so this store does not
      revoke it yet (pass --allow-pending if the schedule is intended)
```

That is correct, not a bug: the stamp is when firmware *starts* refusing
the carrier. A package dated in the future does not protect anyone today.
Either move the stamp (`--stamp-year`) or acknowledge the schedule with
`--allow-pending`.

## 5. Plan the rollout, machine by machine

Delivery is not in this repository, but whether THIS package can land on
THIS machine is not a matter of opinion: it follows from the store image
you already collect. `tools/rollout_plan.py` reads a directory of stores
and answers it per machine, without writing anything:

```
./tools/rollout_plan.py \
    --package /secure/revocation-2026-11/dbx-update.auth2 \
    --cert path/to/dead-leaf.pem \
    --kek-cert path/to/kek.pem \
    --fleet collected/ \
    --canary machine-01.fd \
    --absent path/to/replacement-leaf.pem \
    --at 2026-11-02 \
    --out /secure/revocation-2026-11/plan
```

`--kek-cert` is the certificate the package was signed with, and the tool
verifies the package's PKCS#7 against it before planning anything, so a
plan can never name one key and carry a package signed by another.

```
== rollout plan: 4 machine(s), target CN = Nyrqis dev carrier leaf ...
   package  : /secure/revocation-2026-11/dbx-update.auth2 (1478 bytes, sha256 ...)
   signer   : path/to/kek.pem (sha256 ...) -- the package's PKCS#7 verifies against it
   stamp    : 2027-01-01 -- firmware starts refusing FROM this date, never earlier
   prepared : 2026-11-02 (--at; the plan claims no enforcement before the stamp)
   wave 1 (canary wave -- verify before the rest): machine-01.fd
   wave 2 (fleet): machine-02.fd
   apply   machine-01.fd   revokes the target from 2027-01-01 and the package can land here
           no dbx variable: nothing to advance past, nothing to lose
           KEK carries the signer
   apply   machine-02.fd   revokes the target from 2027-01-01 and the package can land here
           KEK carries the signer
           the store's 1 existing entrie(s) are all carried by the package (a non-destructive write)
   skip    machine-04.fd   already revokes the target through EFI_CERT_X509_SHA256 ...
   blocked machine-03.fd   kek-mismatch: this store's KEK does not carry the ...
   4 machine(s): 2 apply, 1 already-enforcing, 1 blocked (kek-mismatch)
   after each wave: ./tools/verify_dbx_store.py --fleet collected/ --cert path/to/dead-leaf.pem --at 2027-01-01
   plan sha256: 4f1c...
FAIL: 1 of 4 machine(s) cannot take this package as it stands (each reason above is named)
```

Four facts decide each machine, and each is a way a rollout fails:

| Fact | Refusal |
|---|---|
| The package's PKCS#7 is verified, and the signer certificate must appear in THIS store's `KEK` | `kek-mismatch` / `no-kek-variable` — firmware refuses an update signed by a key it does not carry, at `SetVariable` time |
| The package's stamp must be strictly newer than this store's `dbx` timestamp | `stale-stamp` — firmware rejects a non-increasing `EFI_TIME`, so the package is already spent there |
| The write must not un-revoke anything the store already carries | `would-drop-existing-entries` — see below |
| The store must be readable and hold an effective revocation | `unreadable` |

**The one that surprises people:** an `EFI_VARIABLE_AUTHENTICATION_2` write
**replaces** the `dbx` variable; it does not append to it. That is why
Microsoft's shipped dbx update carries the complete list (443 entries at
the time of writing, ADR-0030's inventory) rather than one new entry. The
revocation ceremony in step 2 composes exactly one entry, by its own scope
check, so the package is safe on a store with no `dbx` — or one already
carrying exactly that entry — and destructive on a machine that already
carries other revocations. The plan says so, names how many entries and of
which classes the write would drop, and refuses to call that machine
deliverable. The remedy is a superset list composed over that store's own
entries and re-signed for that class of machine, which is a composition
step this repository does not yet tool; the plan is what makes it visible
before a rollout rather than after it.

`--canary` is the schedule: the named machines form wave 1, the rest wave
2, and after each wave the audit command printed at the bottom is the
gate. A canary that is not in the fleet, already enforces the revocation,
or is itself blocked refuses the plan — a canary the package does not
reach proves nothing.

`--out` writes `rollout-plan.txt` and `rollout-plan.json`. The JSON is
deterministic for a given `--at`, carries one `action` and `category` per
machine, and ends with a `plan sha256` that recomputes from its own
contents: the plan a second person approves is the plan that gets run.
Nothing is written to any store, and nothing is scheduled.

## 6. Deliver it

Delivery is platform process and deliberately not in this repository: which
channel an OS or OEM update service uses, how a rollout is scheduled, and
how many machines are in scope are decisions made where the fleet is.
What the tools give you is an artifact that is correct when it arrives: a
bare PKCS#7 `SignedData` in an `EFI_VARIABLE_AUTHENTICATION_2` descriptor,
signed over `VariableName ‖ VendorGuid ‖ Attributes ‖ EFI_TIME ‖ payload`
with the KEK, carrying exactly one entry for the target.

Whatever the channel, the receiving machine decides for itself: firmware
verifies the AUTH package against the enrolled KEK at `SetVariable` time.
A package signed by a key the store does not trust is refused there — which
is why step 2 names the enrolled KEK explicitly.

## 7. Verify the machines, in both directions

Point the verifier at one store (`virt-fw-vars` reads edk2 varstores) or
at the whole collection in one call:

```
./tools/verify_dbx_store.py --fleet collected/ \
    --cert path/to/dead-leaf.pem \
    --absent path/to/replacement-leaf.pem
```

Every file in `collected/` matching `--fleet-glob` (default `*.fd`) is one
machine's store; the machine is named by its file. The run ends with a
per-machine line and a distribution, which is the part you report:

```
== fleet summary: 1 collection(s), 3 machine(s), target CN = Nyrqis dev carrier leaf
   PASS  machine-01.fd                  enforcing (EFI_CERT_X509_SHA256)
   PASS  machine-02.fd                  enforcing (EFI_CERT_X509_SHA256)
   FAIL  machine-03.fd                  no-dbx-variable: no enforced revocation entry for ...
   3 machine(s): 2 enforcing, 1 no-dbx-variable
FAIL: 1 of 3 artifact(s) do not revoke ... (each fact above is named)
```

The categories are what make the report usable at fleet scale: `enforcing`,
`scheduled`, `no-dbx-variable`, `absent`, `ignored-class`, `over-broad`,
`unreadable`. `--json` carries the same category per machine.

An empty directory is refused (`auditing an empty fleet proves nothing`)
rather than reported as a clean audit, and the run exits non-zero if any
machine is not enforcing.

If the revocation is scheduled for a later date, audit as of that date
with `--at 2027-01-01` instead of `--allow-pending`: `--allow-pending`
says "the schedule is intended", `--at` says "here is the date this
question is being asked about".

`--absent` is the cheap check that catches an over-broad revocation: it
asserts the replacement certificate is **not** revoked in that store. A
revocation that covers the replacement breaks the rotation, and it is
invisible if you only check the dead leaf.

A passing run reports the facts it was decided on:

```
PASS: 2 artifact(s) revoke CN = Nyrqis dev carrier leaf (serial 13452F6F..) through
      an enforced dbx form, effective at 2027-01-01, and do not revoke the 1
      certificate(s) named --absent
```

## What each refusal means

| Message | What happened | What to do |
|---|---|---|
| `the KEK private key has group/world bits set` | Custody is loose | `chmod 600` the key; the ceremony will not use a shared key |
| `the revocation target IS the trust anchor` | You named the root | Revoke the certificate the anchor signs, not the anchor |
| `the signing certificate is not the enrolled KEK` | You signed with a key the store does not trust | Sign with the KEK the store carries, or enroll that key first |
| `the requested stamp (...) is not newer than the previous stamp` | The package would be dead on arrival | Pass a stamp later than `--previous-stamp` |
| `the composed list does not carry the target's To-Be-Signed hash` | The list drifted from the target | Re-run; do not hand-edit the list |
| `this store has no dbx variable at all` (verifier) | Nothing is revoked on that machine | The package never reached it, or it failed `SetVariable` there |
| `... appears as <class> ... the kernel's revocation walk does not read` | The store carries the identity in a class we ignore (ADR-0030) | The machine will **not** refuse the carrier; re-issue in an enforced form |
| `... is revoked only FROM <date>, later than --at` | Scheduled, not in force | Correct if intended (`--allow-pending`); otherwise move the stamp |
| `absent-check ... this store revokes it too` | The revocation covers the replacement | Stop: check the composed list against the intended certificate |
| `--fleet ...: no files match '*.fd'` | The collection directory is empty or the glob is wrong | Point `--fleet` at the stores, or pass `--fleet-glob`; an empty audit is not a clean audit |
| `FAIL  1 of N artifact(s) do not revoke` | At least one machine in the fleet is not enforcing | Read the per-machine lines and the distribution above it; the categories say what to fix |

## What this does not do

- It does not deliver, schedule, or roll back anything. `rollout_plan.py`
  plans a rollout from store images and stops there: it writes no store,
  queues no update and picks no channel. There is no "undo" for a
  revocation that reached a fleet: firmware does not re-trust what dbx
  refuses, so treat step 5 as the last cheap moment.
- It does not enumerate a fleet. You supply the store images, and
  `rollout_plan.py` — like the verifier — reads them read-only.
- It does not compose a non-destructive update for a machine that already
  carries other dbx entries. The ceremony's scope check is exactly one
  entry, so such a machine is reported `would-drop-existing-entries`
  rather than planned; composing the superset list and re-signing it is
  the named open work.
- It does not prove the *firmware* on a machine implements the time-based
  auth surface — acceptance is firmware support, measured for the builds
  in this tree by the `.ms` gate; a machine whose firmware lacks it refuses
  the update with `EFI_INVALID_PARAMETER` (`0x8000000000000002`), which
  looks like a verification failure and is really an unsupported firmware.
  To measure several firmware builds at once instead of one per session,
  `tools/firmware_matrix.sh` runs the same gate against each build you name
  and writes one table of firmware sha256 → expected → measured verdict.
- It does not check the revocation is effective in the stub's own walk as
  well: `verify_dbx_store.py` reads the *store*, and the kernel's walk is
  the second line of defence, not a substitute for the firmware's.

## See also

- [ADR-0029: leaf revocation by dbx TBS hash](../reference/adr/ADR-0029-leaf-revocation-tbs-hash-semantics.md) — the semantics, the two enforced forms, and the composition-vs-update boundary
- [ADR-0030: reject extra dbx hash types](../reference/adr/ADR-0030-dbx-hash-type-scope.md) — why the other classes are reported and not enforced
- [ADR-0028: real keys for ADR-0014](../reference/adr/ADR-0028-real-keys-and-moklist-content.md) — the offline-root hierarchy this ceremony's KEK belongs to

This page is rehearsed: `tools/runbook_rehearsal.sh` runs every step above
on a freshly generated chain (anchor refusal, ceremony, manifest sha256,
scheduled-vs-in-force artifact check, store composition, a clean and a
mixed fleet audit, and the rollout plan with its canary wave and its
`kek-mismatch` refusal) and fails when the page and the tools stop
agreeing. It runs in CI on every push; `tools/rollout_plan_test.sh` proves
each of the planner's own refusals.
