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

This is the same verifier you will point at fleet stores in step 6, run on
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

## 5. Deliver it

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

## 6. Verify the machines, in both directions

Point the verifier at a store collected from the fleet (`virt-fw-vars`
reads edk2 varstores) or at a whole fleet directory:

```
for s in collected/*.fd; do
    ./tools/verify_dbx_store.py --store "$s" \
        --cert path/to/dead-leaf.pem \
        --absent path/to/replacement-leaf.pem
done
```

`--absent` is the cheap check that catches an over-broad revocation: it
asserts the replacement certificate is **not** revoked in that store. A
revocation that covers the replacement breaks the rotation, and it is
invisible if you only check the dead leaf.

A passing store reports the facts it was decided on:

```
PASS: 1 artifact(s) revoke CN = Nyrqis dev carrier leaf (serial 13452F6F..) through
      an enforced dbx form, effective at 2026-11-01, and do not revoke the 1
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

## What this does not do

- It does not deliver, schedule, or roll back anything. There is no
  "undo" for a revocation that reached a fleet: firmware does not re-trust
  what dbx refuses, so treat step 4 as the last cheap moment.
- It does not enumerate a fleet. You supply the store images.
- It does not prove the *firmware* on a machine implements the time-based
  auth surface — acceptance is firmware support, measured for the builds
  in this tree by the `.ms` gate; a machine whose firmware lacks it refuses
  the update with `EFI_INVALID_PARAMETER` (`0x8000000000000002`), which
  looks like a verification failure and is really an unsupported firmware.
- It does not check the revocation is effective in the stub's own walk as
  well: `verify_dbx_store.py` reads the *store*, and the kernel's walk is
  the second line of defence, not a substitute for the firmware's.

## See also

- [ADR-0029: leaf revocation by dbx TBS hash](../reference/adr/ADR-0029-leaf-revocation-tbs-hash-semantics.md) — the semantics, the two enforced forms, and the composition-vs-update boundary
- [ADR-0030: reject extra dbx hash types](../reference/adr/ADR-0030-dbx-hash-type-scope.md) — why the other classes are reported and not enforced
- [ADR-0028: real keys for ADR-0014](../reference/adr/ADR-0028-real-keys-and-moklist-content.md) — the offline-root hierarchy this ceremony's KEK belongs to
