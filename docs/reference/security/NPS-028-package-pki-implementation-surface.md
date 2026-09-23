---
title: Package PKI Implementation Surface
document_id: NPS-028
version: 1.0.0
status: Accepted
classification: Normative
subsystem: security
owners:
  - Nyrqis Architecture
created: 2026-09-21
updated: 2026-09-23
ai_assisted: true
review_cycle: Per implementation milestone
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019, NPS-006, NPS-026, NPS-027, ADR-0018, ADR-0023]
---

# NPS-028 — Package PKI Implementation Surface

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
the **implementation surface** for the package trust machinery whose
*trust model* is specified in `NPS-026` §6 (signature and integrity
requirements) and §6.3 (the publisher key-trust mechanism, decided by
the Architecture Group on 2026-09-21, decision log D3) and whose
*threat analysis* is `NPS-027` (Accepted 2026-09-21, decision log D2).

It exists because NPS-027's acceptance left a named residual: the
planned threat-model phase list is closed, and the **trust machinery**
— key store, enrollment, revocation, root set — had no implementation.
The **signing half** ships and always did: Ed25519 manifest/delta
signatures and a signed repository index (`backend/package_signing.py`,
`backend/update_signing.py`, `backend/package_repo.py`; 37 tests).
**Implementation of the trust machinery has started (2026-09-22, the
same day the scheme was decided):** `backend/package_pki.py` implements
the §3 key store (three collections, §3.3 field set, §3.5
uninstall-as-revocation semantics), the §4 verification pipeline (the
one ordered path, per-stage outcomes, TOFU fail-closed resolution,
the §6.3.4 advisory/block split, §7.2 non-blocking audit sink), the §5
revocation list (root-set-signed, monotonic sequence, replay-refusing,
atomic apply), and §6 enrollment + cross-signed rotation — with the
§6.7.2 fingerprint spelling throughout, and §3.4 custody implemented
the same day (`save_locked`/`load_locked`: ADR-0023 envelope — a
random per-file DEK AEAD-encrypts the canonical store, wrapped by the
Argon2id-derived KEK that is never persisted in plaintext; crate
custody when the keys crate is present; fail-closed — no secret, no
custody file; plaintext `save`/`load` remain as the marked dev/test
path only; 32 tests, `tests/test_package_pki.py`), and §3.2's
store-layer authority enforcement (every store is written 0600 via an
atomic 0600 temp rename — no world-readable intermediate ever exists —
and a load REFUSES a group/world-readable store fail-closed, naming
§3.2 and the fix; 36 tests), the §7 ADR-0018 audit wiring
(`PackageAuditChain` reuses the ContainerManager scheme-2 hash chain
byte-for-byte — differentially pinned against
`ContainerManager._audit_event_content` in the tests — carrying §7.1
records with the package identity, verdict, reason, fingerprint,
publisher, and per-stage outcomes; `make_sink` wires it to the
pipeline; the chain is salt-per-process, JSONL-persisted with the
salt in the header line, and tamper/reorder/removal-evident on
`verify()`), and §5.1's out-of-band transport (`RevocationFetcher` /
`FileRevocationFetcher` plus `refresh_revocations`: the channel is
configured independently of any package-feed object — feed compromise
cannot suppress revocation delivery — and a fetch failure, replay, or
unauthentic list leaves the store untouched). 51 tests), §3.2's daemon-side API half (`DaemonAuthority` +
`PkiDaemonService`: the store's only supported interface, guarded by
an unforgeable authority token the daemon mints — package code can
present no authority, the service exposes no enumeration, and every
mutation is audit-chained; 60 tests total), and the physical IPC
transport (`PkiIpcServer`/`PkiIpcClient`: JSON-lines over a Unix
socket, one server-minted authority — connections are wires, not
identities — and an explicit op allowlist that excludes key-material
reads; deployment hardening built in: 0600 socket mode, fail-closed
refusal of group/world-writable socket directories, and SO_PEERCRED
uid policy (daemon-uid-or-root) where the OS exposes it;
72 tests total), and the daemon's production process model (`PkiDaemonRunner`
+ the `pki serve` CLI subcommand + the shipped `nyrqis-pki.service`
systemd unit: custody is mandatory on the production path — a runner
without an unlock secret is a constructor error and the daemon exits —
the store unlocks at boot, the §7 chain resumes from its persisted
salt header, a clean stop persists custody + chain exactly once, and
the unit wires the §3.2 socket, the StateDirectory-backed custody
store, and the unlock secret's EnvironmentFile; install.sh deploys the
unit and the two-tree mirror rule is contract-pinned; 84 tests total).
Still to build: the §5.3
stale-list
bounds (deferred to implementation validation by design). This
document enumerates the components the remaining implementation must
provide, the requirements each satisfies (REQ-SEC-0003..0006), the
interfaces it must offer, and the attack surfaces it will add. It does
not restate the trust model; it turns it into a buildable checklist.

**ACCEPTED 2026-09-23 (AG decision log D5) — with amendments.** The
Architecture Group's review of the validated surface (§10's 15/15
normative-claims probe, the 217-green package-security run, the
daemon drill that found and closed the §3.2 wire gap) concluded:
accepted with two named amendments, both landed in this revision:

1. **§5.3's deferral is tightened** from frozen-by-design to a named
   thaw trigger: the stale-list posture bounds enter when a real
   revocation channel is configured on a running deployment (the
   `pki serve --revocation-channel` path), with the bound measured
   from that channel's actual delivery cadence — the bounds remain
   unwritten until then, but the thaw is no longer open-ended.
2. **§1/§10 carry the decision-day evidence record**: the acceptance
   rests on the evidence as of 2026-09-23 (the full suite at 6,525
   green / 4 environmental skips, the PKI module at 97 tests, and the
   surface now coexisting with the client-side incident-bundle
   tooling), superseding the 2026-09-22 snapshot.

The prior text is retained below for the record. It is ~~a `Draft`
**by dependency, not by deficiency**~~: its normative
anchors (NPS-026 §6.3, NPS-027) are Accepted, the implementation
exists and has been validated against this document's normative claims
(§10's mechanical evidence table, NPC-002 §5.1/§5.2) — what remained
before this document exited Draft was NPS-026 §9's canonicalization
decision (the one §6.7.3 deferral fencing §2 — still recorded as the
surface's known open fence, gating §6.7.3's wording, not the
mechanisms accepted today) and the Architecture
Group's acceptance review of the validated surface ~~(now concluded, D5)~~. The concrete cryptographic scheme was
**reserved per NPC-002 §6.2** until the dedicated human review
concluded 2026-09-22 (AG decision log D4): the decided scheme is now
normative in NPS-026 §6.7 — this document's §2 fence narrows to the
one deferral §6.7.3 makes (the canonical byte form, pending NPS-026
§9). The
propose-side review package remains at
`docs/00-platform/AG_BRIEF_NPS026_CRYPTO_SCHEME.md` (grounded in the
shipped Ed25519 implementation; its tree claims were re-verified the
day of the review).

## 2. Scope

This document covers the package-security implementation surface only:

- the **key store** (trust anchors, enrolled publisher keys, revoked/expired
  keys) and its persistence and access rules (§3);
- the **verification pipeline** stages every install/update/verify
  touchpoint must run (§4);
- the **revocation distribution channel** (§5);
- the **enrollment flow** and its protected-confirmation requirement (§6);
- the **package-event audit trail** over ADR-0018's mechanism (§7);
- the **new attack surfaces** the implementation itself introduces, to be
  folded into the threat model's surface enumeration (§8).

Out of scope, explicitly: the package manager's dependency resolution,
download, and transaction semantics (NPS-026 §10–§12's other halves, to
be specified by that document's implementation pass); the `.nygi`
image-format verification path beyond the signature stage (NPS-006 §6);
and any concrete cryptographic primitive except what NPS-026 §6.7
freezes (§6.7.1 primitives, §6.7.2 fingerprint); the §6.7.3 canonical
byte form remains open until NPS-026 §9 decides serialization.

## 3. The Key Store

The key store is the root of the §6.3 mechanism; every other component
reads from it and only enrollment writes to it.

3.1. The key store **MUST** hold exactly three logical collections:
platform root anchors (shipped in the OS image per §6.3.1), enrolled
publisher keys (added only through §6 of this document's flow), and
revocation state (expiry dates per §6.3.3, plus the platform revocation
list's entries).

3.2. The key store **MUST** be immutable except through the enrollment
and revocation-update paths. An installed package **MUST NOT** be able
to read, write, or enumerate the store; verification runs in the
daemon's authority, not the container's (the NPS-022 §4 lesson:
capability enforcement must not live where the attacker's code runs).
(Store-layer enforcement landed 2026-09-22: stores are written 0600
atomically and loads refuse group/world-readable stores fail-closed.
The API half of the container-side rule landed the same day:
`PkiDaemonService` is the store's only supported interface — every
read and write demands a `DaemonAuthority` token that only the daemon
can mint (unforgeable constructor, OS-RNG secret) and fails closed
without one, the service exposes no method that returns the store or
enumerates entries, and mutations land in the §7 audit chain. The physical IPC transport landed the same day (`PkiIpcServer` /
`PkiIpcClient`: JSON-lines over a Unix domain socket; the server
mints ONE `DaemonAuthority` — the daemon's identity — and binds the
service to it; connections are wires, not identities; the ops are an
explicit allowlist — `status_for`, `enroll`, `rotate`, `revoke`,
`apply_revocation_list` — and key-material reads are NOT on it,
because verification runs in the daemon's authority and an installed
package has no need, and no right, to export store contents).
The deployment hardening is in the transport itself: the socket file
is chmod 0600 at bind; a group/world-writable socket directory is a
fail-closed bind refusal naming the fix (`chmod go-w`) — a writable
directory lets a local attacker swap or shadow the socket; and where
the OS exposes peer credentials (Linux SO_PEERCRED), connections from
peers that are neither the daemon's uid nor root are dropped at
`setup()` — defense in depth, with the 0600 mode as the floor on
platforms without peer credentials.

3.3. Key store entries **MUST** record: publisher identity, key
fingerprint, enrollment source, enrollment timestamp, expiry date, and
current status (`trusted` / `expired` / `revoked`). The fingerprint is
the identity used in the audit trail (§7) and the enrollment UI (§6) —
one spelling everywhere: **SHA-256(public key), displayed in full as
64 lowercase hex characters** per NPS-026 §6.7.2 (enrollment
confirmations and audit records show the full digest, never a
truncation; the shipped 8-byte `key_id` is a legacy form migrating
with a version marker).

3.4. **Custody.** The store's own integrity **MUST** be protected
equivalently to the vault key manager's custody model (ADR-0023:
Rust-held keys, envelope encryption at rest). The NPS-026 §14 hardware-
root convergence question applies here unchanged: whether the package
root set shares the boot anchor's hardware root stays an open question
in that document and is not prejudged here. (Implemented 2026-09-22:
`save_locked`/`load_locked` — ADR-0023 envelope encryption with the
unlock-secret-derived KEK; plaintext persistence remains as the
explicitly marked dev/test path only.)

3.5. **Uninstall semantics.** Removing an enrolled publisher key
**MUST NOT** silently uninstall or stop verifying already-installed
content at launch (§6.3.4's ownership rule); it **MUST** behave as
revocation does — advisory at launch, hard block at the next
install/update/re-verification touchpoint.

## 4. The Verification Pipeline

Every package-verification touchpoint (install, update, re-verify,
and the NPS-006 §6 `.nygi` mount path) **MUST** run the same ordered
stages; a stage failing means the touchpoint fails, with the reason
reported (the no-silent-failure rule, NPS-006 §6.2's precedent):

1. **Parse and validate the manifest** (structure, checksums — corruption
   detection per FIND-PACKAGE-001's disposition).
2. **Resolve the signing key**: identify the publisher's key by
   fingerprint from the manifest's signature block; fail closed if the
   manifest references a key absent from the store (TOFU rejection,
   §6.3.1 — this is where the FIND-PACKAGE-003 attack dies).
3. **Check key status**: expired or revoked at this instant → hard fail
   (§6.3.3). Expiry is evaluated at verification time, not package time.
4. **Verify the signature** over the manifest and integrity trees
   (NPS-026 §6.2) using the resolved key.
5. **Check the launch-advisory set** (touchpoint-dependent): at install
   and update, a revoked-since-install publisher is a hard fail; at
   launch-time re-verification, it is an advisory notice per §6.3.4.

Stages 2–4 **MUST** be constant-time in the secret-independent sense
and **MUST NOT** leak key material through error messages or timing.
The pipeline **MUST** be the single verification path — no component
bypasses it (NPS-010 §7.1.1's one-mechanism precedent), and the audit
trail (§7) records every touchpoint's stage outcomes.

## 5. Revocation Distribution

5.1. The platform **MUST** distribute the revocation list out-of-band
from package downloads (§6.3.3, the ADR-0014 `dbx` analogue) — same
channel compromise as the package feed must not suppress revocations.

5.2. The list **MUST** be signed by the platform root set and **MUST**
carry a monotonic sequence number; a client **MUST NOT** regress to an
older list (replay-protected), and **MUST** apply a delivered list
atomically.

5.3. Delivery cadence and the stale-list posture (how long a client may
operate on a list older than N) are **implementation validation
questions**: the bounds are intentionally not invented here. **Amended
2026-09-23 (D5):** the thaw trigger is now named — the bounds enter
this document when a real revocation channel is configured on a
running deployment (`pki serve --revocation-channel`), with the bound
derived from that channel's measured delivery cadence. Until then the
bounds remain unwritten, but the deferral is no longer open-ended.

## 6. Publisher Key Enrollment

6.1. Enrollment is the only path by which a key outside the platform
root set becomes trusted (§6.3.2). The flow **MUST** present, in a
protected confirmation (the NPS-015 §5.5 pattern — unspoofable,
suggestion-side confirmation of a consequential act): the publisher
identity, the key fingerprint (§3.3's spelling), and the enrollment
source (where the key came from).

6.2. The confirmation **MUST NOT** be skippable by the enrolling party,
**MUST NOT** be dismissible by timeout alone, and **MUST** log the
completed enrollment — identity, fingerprint, source, and the
confirming actor — as a package-event audit record (§7).

6.3. Rotation (§6.3.5): a cross-signed rotation **MUST** verify the new
key against the old (chain of continuity) and **MUST** record the
rotation linkage in the store entry; a rotation that cannot cross-sign
is a fresh enrollment and follows §6.1 in full.

## 7. Package-Event Audit Trail

7.1. Every verification, install, uninstall, enrollment, and revocation-
update event **MUST** be recorded in the tamper-evident audit log
(ADR-0018's mechanism — the REQ-SEC-0006 disposition, reusing
FIND-AI-002's precedent of one log rather than a new design) with:
package identity, publisher identity and key fingerprint, version,
touchpoint type, and verification outcome per §4's stages.

7.2. Audit records are **append-only and non-blocking**: a recording
failure degrades to a reported warning, never to a failed verification
by itself — the audit trail is evidence, not a verification stage.
(This ordering is deliberate and is the one place a naive reading of
"record everything" would damage the pipeline; it mirrors the
ADR-0018 §6.5 discipline.)

## 8. New Attack Surfaces (for the threat model's next pass)

The implementation will add surfaces NPS-019's catalogue does not yet
carry; they are enumerated here so the next threat-model pass starts
from a list, not a blank page:

- `SURFACE-PKI-0001` — the key store's daemon interface (read paths,
  enrollment write path, revocation updates).
- `SURFACE-PKI-0002` — the revocation distribution channel and its
  client-side application (list replay, suppression, partial delivery).
- `SURFACE-PKI-0003` — the enrollment confirmation flow itself (UI
  spoofing is the FIND-AI-001 class at a different boundary; the
  NPS-015 §5.5 requirement is the control, its enforcement is new).
- `SURFACE-PKI-0004` — the verification pipeline's error reporting
  (reason strings as an oracle; §4's no-leak requirement is the
  mitigation to be validated).

Each maps to `TB-PACKAGE` and, for the store and pipeline, to the
**Malicious package publisher** and **Unprivileged local application**
profiles jointly — the local attacker wants to enroll a key or flip a
status; the remote one wants to suppress a revocation or substitute a
key. Analysis of these surfaces is **future threat-model work**, not
part of this document.

## 9. Requirements Coverage

| REQ | Requirement (ledger wording) | Addressed by |
|-----|------------------------------|--------------|
| REQ-SEC-0003 | Signature-based authenticity per NPS-026 §6 | §4 stages 1–4; NPS-006 §6.3 (implemented + validated, §10) |
| REQ-SEC-0004 | User-enrollable, revocable keys; explicit first-install enrollment | §3, §5, §6 (the mechanism decided in AG decision log D3; implemented + validated, §10) |
| REQ-SEC-0005 | Base/overlay provenance distinction | Out of scope here — NPS-006 §6.4 owns it; §4 stage 5 consumes its verdict |
| REQ-SEC-0006 | Package events in the tamper-evident audit log | §7 (implemented + validated, §10) |

## 10. Implementation Validation Record

Recorded 2026-09-22, the same day the last mechanism wired in. Per
NPC-002 §5.1/§5.2, every claim below was verified against the shipped
tree by a mechanical probe (importable assertions, not prose), and
every count is reproducible from `tests/`.

| Normative claim (this document) | Evidence in the tree |
|---------------------------------|----------------------|
| §6.7.2 fingerprint = SHA-256(pub), full 64 lowercase hex | probe: `key_fingerprint` output equals the recomputed digest, 64 lowercase chars |
| §3.1 exactly three collections + monotonic sequence state | probe: `_roots`/`_enrolled`/`_revocations`/`revocation_sequence` present; `tests.test_package_pki` §3 tests |
| §3.2 store written 0600 via atomic rename; loads refuse group/world-readable | probe: mode check after `save`; `PkiError` raised on a 0640 store at `load` |
| §3.4 custody envelope; unlock secret never at rest | probe: envelope fields (`magic`, scheme, KEK blob, wrapped DEK, payload) present; secret string absent from the custody file |
| §3.2 `DaemonAuthority` unforgeable; allowlist excludes key reads | probe: direct construction raises; `enrolled_public_key_for`/`root_public_key_for` not in `IPC_ALLOWED_OPS` |
| §4 the one ordered path, per-stage outcomes, fail-closed | probe: stages `1_parse_manifest` → `2_resolve_key` recorded in order, TOFU denial carries its reason |
| §5.1 channel independent of the feed; failure never mutates the store | probe: refresh against a missing channel returns `fetch_failed`, sequence unchanged; the daemon loop is `FileRevocationFetcher`-shaped (path-only config) |
| §5.2 replay-refusing atomic apply | `tests.test_package_pki` §5.2 tests (regressive list refused, store intact) |
| §6.2 confirmation not skippable; spoof-refusing | probe: unconfirmed enrollment raises; spoofed-fingerprint confirmation raises in-process AND over the wire (`TestPkiIpcWireConventions`) |
| §7 tamper-evident chain; §7.2 sink failure never changes a verdict | probe: `verify()` clean then false after an entry mutation; a raising sink degrades to an `audit: warn` stage record |
| §3.2/§3.4 production assembly end-to-end | the 2026-09-22 daemon drill: custody-mandatory boot, IPC enrollment, SIGTERM persistence, restart survival (v0.9.1 revision entry) |

Suite evidence: `tests.test_package_pki` **97 tests**, package-security
set (`test_package_pki` + `test_package_repo` 16 + `test_package_signing`
20 + `test_update_signing` 11 + `test_repo_journal_vfs` 69 + the
scheduled-runs contract 4) **217 green** in one run; the PKI module
verifies byte-identically from a clean `git worktree` checkout of the
landed commit. The 15-claim probe above passed 15/15 (the one
initially-failing probe row was a probe bug — it fed stage 1 an empty
manifest, so the pipeline correctly never reached stage 2; the
corrected probe confirms the ordered path).

REQ-SEC-0005's exclusion is deliberate: this document's boundary is the
signature/key machinery; overlay provenance is image verification and
belongs to the NPS-006 implementation pass. The coverage table exists
so the requirements ledger can cite a real section for each REQ the
moment implementation begins.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 0.1.0   | 2026-09-21 | Initial draft — the PKI implementation surface NPS-027's acceptance left as its residual: key store, verification pipeline, revocation channel, enrollment flow, audit trail, and the four new surfaces enumerated for the threat model's next pass |
| 0.2.0   | 2026-09-22 | The scheme was decided (NPS-026 v1.3.0 §6.7, D4) and implementation STARTED: `backend/package_pki.py` — the §3 key store (three collections, §3.3 fields, §3.5 uninstall-as-revocation), the §4 pipeline (one ordered path, per-stage outcomes, TOFU fail-closed, §6.3.4 advisory/block split, §7.2 non-blocking audit sink), the §5 revocation list (root-signed, monotonic, replay-refusing, atomic apply), §6 enrollment + cross-signed rotation, the §6.7.2 fingerprint spelling throughout; 26 tests. Remaining: §3.4 custody, §3.2 daemon-authority enforcement, §7 ADR-0018 wiring, §5.1 transport, §5.3 bounds |
| 0.3.0   | 2026-09-22 | §3.4 custody LANDED: `save_locked`/`load_locked` — ADR-0023 envelope encryption at rest (random per-file DEK AEAD-encrypts the canonical store; DEK wrapped by the Argon2id-derived KEK, which is never persisted in plaintext; AEAD contexts bind the format's magic so payloads cannot relocate between files or formats; crate custody when the keys crate is present, the documented floor otherwise; fail-closed — no secret, no custody file, and plaintext persistence demoted to the explicitly marked dev/test path). 6 custody tests (32 total). Remaining: §3.2 daemon-authority enforcement, §7 ADR-0018 wiring, §5.1 transport, §5.3 bounds |
| 0.4.0   | 2026-09-22 | §3.2 store-layer authority enforcement LANDED: every store write goes through a 0600 atomic temp-rename (no world-readable intermediate ever exists) and every load refuses a group/world-readable store fail-closed, naming §3.2 and the fix. 4 authority tests (36 total). Remaining: §3.2's daemon-side half (host the store behind the daemon's IPC), §7 ADR-0018 wiring, §5.1 transport, §5.3 bounds |
| 0.5.0   | 2026-09-22 | §7 ADR-0018 wiring + §5.1 transport LANDED: `PackageAuditChain` (the scheme-2 chain over package events, byte-identical to ContainerManager's construction and differentially pinned against it; §7.1 records carry package_id/version/verdict/reason/fingerprint/publisher/stages; salt-per-process with the salt persisted in the JSONL header so loaded chains re-verify; tamper/reorder/removal evidence via `verify()`; `make_sink` attaches it to the pipeline under §7.2's never-changes-a-verdict rule) and `RevocationFetcher`/`FileRevocationFetcher`/`refresh_revocations` (the §5.1 channel is configured independently of the package feed; fetch failure, replay, or unauthentic list leaves the store untouched; the store now persists its §5.2 `revocation_sequence` so monotonicity survives restarts). 15 new tests (51 total). Remaining: §3.2's daemon-side half, §5.3 bounds |
| 0.6.0   | 2026-09-22 | §3.2's daemon-side API half LANDED (SURFACE-PKI-0001's first build): `DaemonAuthority` (unforgeable — direct construction raises, `mint()` is the daemon's only entry, OS-RNG secret, instances compare by secret) and `PkiDaemonService` (the store's ONLY supported interface: every read/write demands a valid — and once `bind()`ed, matching — authority and fails closed without one; no method returns the store, the key material, or an enumeration; enroll/rotate/revoke/apply-revocation-list mutations land in the §7 audit chain). §3.2's remaining piece is the physical IPC transport at daemon integration. 9 new tests (60 total). Remaining: §3.2's IPC transport, §5.3 bounds |
| 0.7.0   | 2026-09-22 | §3.2's physical IPC transport LANDED (SURFACE-PKI-0001 complete at the API+transport layer): `PkiIpcServer`/`PkiIpcClient` — JSON-lines over a Unix domain socket. The server mints ONE `DaemonAuthority` at start (the daemon's identity) and binds the service to it; connections are wires, not identities, and no client ever sees a token. The ops are an explicit allowlist (`status_for`, `enroll`, `rotate`, `revoke`, `apply_revocation_list`) — key-material reads are NOT on it, because verification runs in the daemon's authority and an installed package has no need (and no right) to export store contents. Unknown and private-name ops refused; mutations over the wire stay audit-chained; the transport does not widen what the service allows. 8 new tests (68 total). Remaining: the production daemon deployment (peer-credential hardening, socket-directory permissions), §5.3 bounds |
| 0.8.0   | 2026-09-22 | §3.2 deployment hardening LANDED in the transport: the socket file is chmod 0600 at bind (owner-only from the instant it exists); a group/world-writable socket directory is a fail-closed bind REFUSAL naming the fix (`chmod go-w`) — a writable directory lets a local attacker swap or shadow the socket; and where the OS exposes peer credentials (Linux SO_PEERCRED) connections from peers that are neither the daemon's uid nor root are dropped at `setup()`, with the 0600 mode as the documented floor on platforms without peer credentials. `stop()` made idempotent (no socket leak on shutdown). 4 new tests (72 total). Remaining: §5.3 bounds (frozen by design), the daemon's production process model (deployment, not surface) |
| 0.9.0   | 2026-09-22 | The daemon's production process model LANDED (the last §3.2/§3.4 assembly item): `PkiDaemonRunner` — custody is MANDATORY on the production path (a runner without an unlock secret is a constructor error; a custody file with no secret at boot refuses to start), the store unlocks at boot (or is created on first start), the §7 chain resumes from its persisted salt header and re-verifies, and a clean stop persists custody + chain exactly once (idempotent); the `pki serve` CLI subcommand assembles it with signal-flag polling (not `signal.pause()` — the default disposition would kill the process mid-handler, skipping persistence) and installs as the `nyrqis-pki.service` systemd unit (DynamicUser, NoNewPrivileges, ReadOnlyPaths=/opt/nyrqis, StateDirectory for the custody store + audit chain, the unlock secret from the optional EnvironmentFile — without it the daemon exits by design); install.sh deploys the unit and the two-tree mirror rule is contract-pinned (5 new wiring tests, 84 total). Remaining: §5.3 bounds (frozen by design) |
| 0.9.1   | 2026-09-22 | The end-to-end daemon drill (boot `pki serve` as a real subprocess, enroll over the §3.2 socket, SIGTERM, restart, verify persistence) FOUND a gap the transport's own tests had missed: no test ever exercised `enroll` over the wire, and JSON carries no bytes — the 64-char hex string arrived where the service demands 32 raw bytes, and the §6 confirmation dataclass arrived as a plain dict. The binary-over-JSON conventions are now explicit and fail-closed: named hex params (`public_key`) decode to bytes server-side (malformed hex = request failure, never a silent string pass-through), dataclass params (`confirmation`) rebuild from their field mapping (unknown fields = request failure), and `PkiIpcClient` hex-encodes bytes arguments on send. 5 new wire-convention tests (89 PKI total; 102 with the deployment guards). The drill itself passed end to end: custody-mandatory boot, IPC enrollment, spoof/hex/shape refusals, SIGTERM persistence (custody + salted §7 chain), restart survival |
| 0.9.2   | 2026-09-22 | §5.1 wired into the daemon (the surface's last unwired mechanism): `PkiDaemonService.refresh_revocations` — the daemon's own authority drives the out-of-band refresh (callers cannot smuggle a fetcher through the IPC allowlist; the op is deliberately daemon-internal), applied lists are audit-chained as `pki_apply_revocations` with an out-of-band source marker and rejected lists as `pki_refresh_revocations` evidence (never an error path), and a service-level lock serializes the background refresh against in-process mutations; `PkiDaemonRunner` gained the background refresh loop — a channel-configured daemon thread (`FileRevocationFetcher`, independent of any package feed by construction) that fails open per §5.1 (a bad fetch or bad list never touches the store; the outcome is evidence) and is joinable at stop; `pki serve --revocation-channel/--refresh-interval` (disabled by default — an absent channel is an operator decision, not an omission) and the unit ships the channel path. 8 new tests (97 PKI total) |
| 0.9.3   | 2026-09-22 | The implementation-validation pass RECORDED (§10, new): a mechanical 15-claim probe verifying this document's normative statements against the shipped tree — fingerprint spelling, the three collections, 0600 store writes and fail-closed loads, the custody envelope with no secret at rest, the unforgeable authority, the key-read exclusion from the IPC allowlist, the ordered §4 path with per-stage outcomes, §5.1's store-untouched-on-failure rule, §5.2 replay refusal, the §6.2 confirmation gates (in-process and over the wire), §7 tamper evidence, and §7.2's sink-failure rule — 15/15 after correcting one probe bug (the probe, not the pipeline, fed stage 1 an empty manifest). §9's coverage table now marks the implemented rows. This is the evidence base NPC-002 §5.1/§5.2 require; the document's remaining Draft dependency is NPS-026 §9's canonicalization decision, not implementation |
| 1.0.0   | 2026-09-23 | **ACCEPTED (AG decision log D5) — with amendments.** The review sat on the §10 evidence (15/15 probe, 217-green package-security run, the drill-closed wire gap). Amendments landed: §5.3's deferral tightened to a named thaw trigger (bounds enter when a real revocation channel runs in production, derived from its measured cadence); §1 carries the decision-day evidence snapshot (suite 6,525 green, PKI module 97). The NPS-026 §9 canonicalization fence remains recorded — it gates §6.7.3's wording, not the accepted mechanisms |

---
**End of Document**
