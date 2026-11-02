---
title: ADR Index
document_id: NPC-005
version: 1.38.0
status: Draft
classification: Reference
owners:
  - Nyrqis Architecture
created: 2026-07-12
updated: 2026-11-02
ai_assisted: true
review_cycle: Continuous
depends_on: [NPC-001]
---

# NPC-005 — Architecture Decision Record Index

Architecture Decision Records (ADRs) capture a specific decision, the
alternatives considered, and the reasoning — per NPC-001 §6. Full ADR text
lives under `docs/reference/adr/ADR-XXXX-slug.md`. This index tracks status
only.

| ID | Title | Status | Date | Supersedes |
|----|-------|--------|------|------------|
| ADR-0001 | Adopt Diátaxis + MkDocs Material for documentation | Accepted | 2026-07-12 | — |
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Accepted | 2026-07-13 | — |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Accepted | 2026-07-13 | — |
| ADR-0004 | Containerized execution model for all application classes | Accepted | 2026-07-13 | — |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Accepted | 2026-07-13 | — |
| ADR-0006 | Adopt a hybrid microkernel as the Nyrqis kernel base | Accepted | 2026-07-13 | — |
| ADR-0007 | Adopt Zstandard as the default compression codec | Accepted | 2026-07-12 | — |
| ADR-0008 | Adopt an AOSP-based container runtime for Android compatibility | Accepted | 2026-07-13 | — |
| ADR-0009 | Per-container token-bucket rate limiting for IPC | Accepted | 2026-07-12 | — |
| ADR-0010 | Adopt Vulkan as the native graphics API foundation | Accepted | 2026-07-13 | — |
| ADR-0011 | AI assistant runs as an ordinary capability-scoped container | Accepted | 2026-07-13 | — |
| ADR-0012 | Adopt NyHAL as a pluggable kernel abstraction layer | Accepted | 2026-07-13 | — |
| ADR-0013 | Adopt an EEVDF-derived scheduler with a real-time priority class | Accepted | 2026-07-13 | — |
| ADR-0014 | Adopt UEFI Secure Boot with user-enrollable keys | **Accepted** | 2026-10-11 | — |
| ADR-0015 | Shared dynamic binary translation approach for ARM/x86 compatibility | Proposed | 2026-07-13 | — |
| ADR-0016 | NyFS Linux Backend implemented as a user-space FUSE filesystem | Accepted | 2026-07-13 | — |
| ADR-0017 | Reject domain-grouped NPS renumbering | **Rejected** | 2026-07-13 | — |
| ADR-0018 | Hash-chained append-only log for capability audit records | Accepted | 2026-07-13 | — |
| ADR-0019 | Journal commit as the default NyFS save() mode | **Accepted** | 2026-09-30 | — |
| ADR-0020 | Implementation languages and the platform boundary | **Accepted** | 2026-08-13 | ADR-0020 v1 superseded by v2 (Python and Rust, 2026-08-12) |
| ADR-0021 | NyRuntime direction — IPC serving loop behind the FFI boundary | **Accepted** | 2026-08-15 | — |
| ADR-0022 | NyVault — storage as a daemon-hosted service on the IPC transport | Accepted | 2026-08-15 | — |
| ADR-0023 | NyVault key manager — envelope encryption with Rust-held key custody | Accepted | 2026-08-15 | — |
| ADR-0024 | Streaming data plane — chunked framing for large CALL payloads | **Accepted** | 2026-10-11 | — |
| ADR-0025 | NUI runtime consumption | Accepted | 2026-08-16 | status cell added 2026-09-20 — the index row itself was missing (caught by the frontmatter-vs-index sweep; its 2026-09-06 frontmatter flip is NOT recorded in any Group decision log — same flag as ADR-0019, see that row) |
| ADR-0026 | Wayland Display Server Integration for the Nyrqis Shell | Accepted | 2026-09-01 | status cell added 2026-09-20 — the index row itself was missing; Accepted by implementation record (M13's display-server integration milestone, roadmap-checked), no separate AG session noted |
| ADR-0027 | Wire-verified protocol constants and fail-closed acceptance gates | **Accepted** | 2026-09-16 | — |
| ADR-0028 | Real keys for ADR-0014 — the one-hop hierarchy and the MokList content decision | **Accepted** | 2026-10-29 | — |

## ADR Statuses

- **Proposed** — open for discussion, not yet binding.
- **Accepted** — binding; implementation MUST conform.
- **Superseded** — replaced by a later ADR (see `Supersedes` column).
- **Rejected** — considered and declined; kept for historical record.

## Revision History

| Version | Date       | Change              |
|---------|------------|----------------------|
| 1.0.0   | 2026-07-12 | Initial index        |
| 1.1.0   | 2026-07-12 | Add ADR-0006 (kernel base selection) |
| 1.2.0   | 2026-07-12 | Add ADR-0007 (compression codec selection) |
| 1.3.0   | 2026-07-12 | Add ADR-0008 (Android runtime approach) |
| 1.4.0   | 2026-07-12 | Add ADR-0009 (IPC rate limiting) |
| 1.5.0   | 2026-07-12 | Add ADR-0010 (Vulkan graphics foundation) |
| 1.6.0   | 2026-07-12 | Add ADR-0011 (AI assistant containerization) |
| 1.7.0   | 2026-07-12 | Add ADR-0012 (NyHAL pluggable kernel backend) |
| 1.8.0   | 2026-07-13 | Milestone 9 review: accept ADR-0002/0003/0004/0005/0006/0008/0010/0011/0012; add ADR-0013 (scheduler algorithm, Proposed — tuning-blocked). ADR-0007 and ADR-0009 remain Proposed pending benchmark data. |
| 1.9.0   | 2026-07-13 | Add ADR-0014 (secure boot), ADR-0015 (shared ARM translation), ADR-0016 (NyFS Linux Backend FUSE strategy) — closing three of the backlog's open architecture-decision items |
| 1.10.0  | 2026-07-13 | Add ADR-0017 — the project's first Rejected ADR, declining a proposed NPS domain-renumbering scheme |
| 1.11.0  | 2026-07-13 | Add ADR-0018 (hash-chained audit log), from threat model Phase 3 |
| 1.12.0  | 2026-08-12 | Add ADR-0019 (journal commit as the default save() mode) — the Architecture Group review package for the 2026-08-12 implementer default flip |
| 1.13.0  | 2026-08-12 | Add ADR-0020 (Python and Rust as the implementation languages) — first recorded language strategy |
| 1.14.0  | 2026-08-13 | ADR-0020 v2.0.0 — canonical language matrix (Rust/C++/C platform languages; NyHAL resolved Rust-first) + platform-boundary principle (platform-critical execution paths must not depend on the Python interpreter) |
| 1.15.0  | 2026-08-13 | ADR-0020 **Accepted** by Architecture Group (issue #2) — matrix and boundary rule binding |
| 1.16.0  | 2026-08-15 | Add ADR-0021 (NyRuntime direction — IPC serving loop behind the FFI boundary); ADR-0021 **Accepted** — close gate met |
| 1.17.0  | 2026-08-15 | Add ADR-0022 (NyVault storage service) and ADR-0023 (NyVault key manager) |
| 1.18.0  | 2026-08-16 | Add ADR-0024 (streaming data plane — chunked framing for large CALL payloads) |
| 1.19.0  | 2026-09-10 | ADR-0009 v1.3.1: Implementation Note — per-sender fairness implemented (`FairTokenBucket`, fair-by-default endpoints, operator control-plane ops, dynamic-shares opt-in), §32b–d benchmark record complete, review package published (`ADR-0009-review-package.md`) |
| 1.20.0  | 2026-09-16 | Add ADR-0027 (wire-verified protocol constants and fail-closed acceptance gates) — **Accepted** |
| 1.21.0  | 2026-09-20 | Add the missing ADR-0025/0026 status rows; correct six stale adr/README.md cells (0007/0009/0013/0016/0022/0023 still read pre-2026-09-19-review Proposed); the ADR-0019 frontmatter reverted to Proposed per its own governance line and this index (its 2026-09-06 flip to Accepted was not sanctioned by any Group record) — caught by the frontmatter-vs-index status sweep |
| 1.22.0  | 2026-09-21 | ADR-0009 v1.3.2 — the standing dynamic-shares-as-default item decided (AG decision log D1): static default retained; nothing remains open in the ADR. Frontmatter version caught up to this table (it had lagged at 1.18.0 through the 1.19–1.21 entries) |
| 1.23.0 | 2026-09-30 | ADR-0019 **Accepted** — RATIFIED AS-IMPLEMENTED, Bundle D decided (AG decision-log row D1, owner direction via the recorded session, the E1/F1 same-day shape): all three issue-#1 ledgers resolved per AG_BRIEF_ADR0019's recommendation (60 s cadence retained; `auto_compact=True` ratified — the ADR-0022 as-implemented shape; shutdown ordering confirmed); the watcher resource profile recorded as a tuning follow-up, not a blocker. The sanctioned flip supersedes the 2026-09-06 unsanctioned self-flip reverted 2026-09-20 |
| 1.24.0 | 2026-10-11 | ADR-0014 **Accepted** — AG decision-log row I3 (owner direction via the recorded session, the E1/F1/G1 shape), as proposed with no text amendments. Was never named a gate ("not benchmark-blocked" per REPOSITORY_STATE's own record); the adopted D3 key-trust mirror conforms to its postures; its two Consequences work items become properly-scoped Phase-10 follow-ups |
| 1.25.0 | 2026-10-11 | ADR-0024 **Accepted** — AG decision-log row J2 (owner direction via the recorded session), as proposed: both increments implemented same-day (0.14.20/0.14.21) and benchmarked (§29; the post-0.14.21 §27 re-measurement collected 2026-09-20), the evidence set per its own review-input record complete; the two review-input caveats land verbatim in its Consequences |
| 1.26.0  | 2026-10-29 | Add ADR-0028 (real keys for ADR-0014 — the one-hop offline-root hierarchy and the MokList carries-the-leaf content decision) — **Accepted** by implementation record (the two ADR-0014 Consequences work items it closes were named properly-scoped Phase-10 follow-ups by ADR-0014's own accepted record, the ADR-0026 precedent) |
| 1.27.0  | 2026-10-29 | Add ADR-0029 (leaf revocation by dbx TBS hash — the enforcement semantics the `.ms` gate's firmware-served revocation leg now pins) — **Accepted** by implementation record, the ADR-0026/2028 precedent |
| 1.28.0  | 2026-10-30 | ADR-0029 v1.2.0: Implementation Note — the compromise procedure has tooling (`tools/rotate_leaf_ceremony.sh`: replacement leaf issuance + the dead leaf's dbx TBS-hash entry, scope/serial/chain/carrier/store checks, both a CI step and covered by a refusal-path test); the `.ms` gate carries TWO firmware-served revocation legs (hash-typed and whole-certificate, one per dbx parser branch) plus a stub-side shape probe, each ASSERTING the refusing party and the served bytes; and the entry's payload FIELD ORDER is corrected — `EFI_CERT_X509_SHA256` is digest-then-EFI_TIME (UEFI 2.10 32.4.1, edk2 `ImageAuthentication.h`), while this repo's generator and parser had them swapped and agreed only with each other, which is why no real firmware matched; fixed in `patch_carrier_negatives.py` + `dbx.rs` and re-proven on real firmware, where BOTH dbx forms are now refused by the firmware itself (superseding the earlier "whole-cert only" reading, which was the field-order defect) |
| 1.29.0  | 2026-10-31 | Add ADR-0030 (reject extra dbx hash types in the enforced revocation surface) — **Rejected**: the seven other UEFI 2.10 32.4.1 entry classes stay REPORTED facts, now pinned by the `dbx-skip-types` fixture control, and widening the surface requires a superseding ADR |
| 1.30.0  | 2026-10-31 | ADR-0029 v1.3.0: Implementation Note — the UPDATE side's ACCEPTANCE half is measured on real firmware (a KeK-signed package written through SetVariable, stored, and read back; its non-KeK twin refused), and closing it exposed two defects in this repo's own builder: the signed content is `VariableName‖VendorGuid‖Attributes‖EFI_TIME‖Data` (not `Data‖EFI_TIME`), and the descriptor carries a BARE PKCS#7 `SignedData` (not `openssl`'s `ContentInfo` wrapper). Both were self-consistent with the host gate and refused by edk2; the gate now pins the layout byte-wise from spec strings and cross-checks it against `sbvarsign` |
| 1.31.0  | 2026-10-31 | ADR-0030 v1.1.0: Recheck — the "what vendors ship" premise is now an inventory, not prose (`tools/dbx_inventory.py` over Microsoft's published artifacts). Measured: the current amd64 dbx update is 443 `EFI_CERT_SHA256` entries and nothing else; the 2024 package's CA revocation is ONE whole-certificate `EFI_CERT_X509` entry; the per-architecture inventory JSON is 677 SHA-256 image hashes and no 384/512 or TBS-hash form at all. The **Rejected** decision stands (reopen condition unmet), and the ADR's own claim that Microsoft CA revocations use `EFI_CERT_X509_SHA256` is corrected: that type is enforced because ADR-0028/0029's own rotation issues it, while the shipped CA revocation is covered by the `EFI_CERT_X509` branch |
| 1.32.0  | 2026-10-31 | ADR-0029 v1.4.0: Implementation Note (second) — the signing/distribution half. `tools/sign_dbx_update_ceremony.sh` composes + signs a revocation for a named certificate and refuses on custody, anchor-target, signer-identity, stamp-monotonicity, list-scope and binding violations (`tools/update_ceremony_refusals_test.sh`: positive control + five named refusals, a CI step). Plus a CROSS-BUILD control: the tree's non-.ms OVMF refuses the same KeK-signed package with `EFI_INVALID_PARAMETER` (it has no time-based-auth surface), so acceptance is pinned as that firmware's support via opt-in `SECBOOT_CROSS_BUILD_CODE`; writing it exposed that the dbx shape probe's result was computed but never in the PASS condition |
| 1.33.0  | 2026-11-01 | ADR-0029 v1.5.0 + ADR-0030 v1.2.0: Implementation Note (third) — the FLEET end. `tools/verify_dbx_store.py` answers "does this store refuse that certificate" for an edk2 varstore (via `virt-fw-vars --output-json`) or a bare list/auth2 package, distinguishing revoked-effective / revoked-not-yet / present-in-a-class-the-walk-ignores / absent, with `--absent` catching an over-broad revocation; its To-Be-Signed walk is a SECOND implementation cross-checked against the signing path, and `tools/verify_dbx_store_test.sh` (CI, every push) proves both enforced forms pass and five refusals bite. Verified against the gate's own firmware-composed stores (both dbx forms) and the ceremony package (refused as scheduled, by design). Operator sequence documented in `docs/how-to/revoke-a-secure-boot-carrier.md`. Also measured: the `.ms` gate's acceptance assertions hold on a THIRD firmware build — Debian trixie's edk2 2025.02 (`1a462955..`, its own vars template) — full gate `EXIT=0`, with the same ACCEPTED 0x5c6/read-back/non-KeK-refused sequence; the two in-tree images are byte-identical to Ubuntu's 2024.02 package, so this is the first build measured beyond it |
| 1.34.0  | 2026-11-01 | ADR-0029 v1.6.0 (row backfilled 2026-11-02; the previous round's commit recorded the version in REPOSITORY_STATE without bumping the ADR): Implementation Note (fourth) — the CROSS dimension becomes an artifact. `tools/firmware_matrix.sh` runs the unmodified `.ms` gate against every firmware build named and writes one table of firmware sha256 → expected → measured verdict, refusing a duplicated build, a mislabelled expectation, a transcript not pinned to its image, and an inherited `SECBOOT_CROSS_BUILD_CODE`; measured `MATRIX EXIT=0` on four builds (two enforcing edk2 versions accepting the KeK package with read-back verified, two without the time-based-auth surface answering `EFI_INVALID_PARAMETER`). `verify_dbx_store.py --fleet` reports one verdict per machine plus the distribution by category and refuses an empty collection; `tools/runbook_rehearsal.sh` executes the revocation how-to end to end on a rebuilt chain, and found `build.sh` failing on a fresh tree (the stray certificate existed only as PEM while build.sh hashed its DER form) |
| 1.35.0  | 2026-11-02 | ADR-0029 v1.7.0: Implementation Note (fifth) — the ROLLOUT plan. `tools/rollout_plan.py` answers, per machine from a collected store, whether a signed package can land there and from what date the machine enforces: STATE (the fleet verifier's own verdict — `skip`), KEK (the package's PKCS#7 verified against `--kek-cert` and the signer required in THIS store's KEK — `kek-mismatch`/`no-kek-variable`), STAMP (strictly newer than the store's dbx `EFI_TIME`, field by field — `stale-stamp`), and REPLACEMENT. The load-bearing finding: an auth2 write REPLACES the dbx variable, so the ceremony's one-entry package is destructive on a machine that already carries other revocations (`would-drop-existing-entries`, with the classes counted; the superset composition is named as untooled, and the one-entry scope check is deliberately not widened). `--canary` forms wave 1 and the rest wave 2; `--out` writes a deterministic plan whose sha256 recomputes from its own contents; stores are read-only. `tools/rollout_plan_test.sh` (CI, every push) proves every verdict and both artifact properties with named lines, and the runbook rehearsal now executes the page's new step 5 on a fresh chain. Measured on this tree's own stores, it reported the true fact that the non-`.ms` store's KEK does not carry the dev anchor |
| 1.38.0  | 2026-11-02 | ADR-0031 v1.2.0: Implementation Note — the input increment and the live ISO. `ps2-aux` is bound: release 1-71's `input.rs` kernel half becomes `src/ps2_aux.rs` (i8042 aux enable, IRQ12/vector 0x2C with the cascade, 3-byte packet assembler with signed deltas, ACK/device-ID protocol words) and proves the line by asking the device for its ID rather than by asserting presence; measured `ps2-aux bound`, id 0x00, bytes via IRQ12, autoload 5 bound / 0 unbound. Two defects are recorded because both were mine: the polled setup lost its replies to the IRQ1 keyboard handler until it ran with interrupts off, and `0xFA` (bit 3 set) was assembled as a packet start and swallowed the ID. `tools/make_live_iso.sh` builds a per-mode GRUB/Multiboot2 live disc recording the image digest AND the kernel digest it carries; `tools/check_live_iso.sh` verifies integrity, attribution (a stale image fails) and optionally a headless boot (measured: 22 MiB, all three PASS). BIOS/CSM only by decision: GRUB-EFI would bypass the signed-stub chain |
| 1.37.0  | 2026-11-02 | ADR-0031 v1.1.0: Implementation Note — the display handoff and the shell render. The UEFI stub reads the firmware's GOP mode and writes Multiboot2 tag 8 plus the scan-out-order vendor tag (`0x7d2`), refusing (with a named reason) on absent GOP, no current mode, no mode info, `BltOnly`/palette, an unsupported bit-mask layout, or empty geometry; measured `GOP 1280x800 @ 0x80000000 BGRX` with the driver bound and the mode read-back verified on the OVMF gate, so both boot paths now reach the same driver. Release 1-71's `shell/src/ui.rs` + `font_data.rs` are compiled into the kernel from the port tree (`#[path]`, one copy) and drawn into each mode's content pane, so the shell's own size-class logic yields a different real layout per mode (phone pane = `Compact`, 0 quick-setting tiles; desktop = `Expanded`, 3), proven by QEMU screendumps looking for the clock widget's `container` fill (a colour unique to the shell) after the first probe was found to equal the renderer's own background. The session gap is unchanged: no input routing, no user-space owner until the `0x84` gfx/input calls are ported |
| 1.36.0  | 2026-11-02 | Add ADR-0031 (merge release 1-71 into this kernel) — **Accepted**: this repository's kernel and UEFI/Secure Boot chain stay the base (ADR-0014/0028/0029), release 1-71's BIOS-side verification is imported for reference only, and the shell/services/`desk`/input stack is imported in-tree under `porting/release-1-71/` with a per-file port manifest and the measured ABI gap (`int 0x84` service surface, absent here). Landed with it: a probe/autoload driver registry that reports undriven controllers as `unbound` (framebuffer and PCI ported; PS/2 aux and USB HID staged), and four real device modes (desktop/tablet/phone/console) as one renderer with four layout contracts, selected per machine and verified by independent QEMU screendumps |

---
**End of Document**
