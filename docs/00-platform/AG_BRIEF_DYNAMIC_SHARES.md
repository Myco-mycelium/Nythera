---
title: AG Decision Brief — Standing Item D1, Dynamic Shares as the Default
document_id: AG-BRIEF-2026-09-D1
version: 1.0.0
status: Pre-read — awaiting the Group's decision (see AG_AGENDA.md, standing items)
owners: [Nyrqis Architecture]
created: 2026-09-20
ai_assisted: true
depends_on: [AG-AGENDA-2026-09, ADR-0009, NPS-010]
---

# AG Decision Brief — Standing Item D1: Dynamic Shares as the Default

**What this document is:** an AI-drafted pre-read for the standing
item registered in `AG_AGENDA.md` (ADR-0009 follow-on — dynamic
`fair_shares` as the endpoint-bucket default). The decision ledger is
complete: every cell of the static-vs-dynamic trade has a measured
number (`tests/BENCHMARK_RESULTS.md` §32e, 2026-09-10; §32f,
2026-09-20; re-verified on-host 2026-09-20).

**What this document is not:** a decision. Per NPC-001 §11.1 the
suggest-vs-act boundary applies — the recommendation below is
overridable Group judgment, and nothing in the tree changes until the
Group's decision is recorded in `AG_AGENDA.md`'s decision log.

---

## The decision required

ADR-0009 was Accepted (2026-09-19) with the shipped posture: **static
`fair_shares` default, dynamic opt-in** (decision log A1, second row).
The one open policy question is whether dynamic mode should become the
**default** for new endpoints instead.

## The measured ledger (complete — nothing left to benchmark)

| scenario (256 / 2,000/s envelope, shares=8, sender_burst=64) | static shares | dynamic shares |
|---|---:|---:|
| lone full-speed sender | 313.0 calls/s (share-capped: `sender_burst + envelope/shares`) | **2,063.0 calls/s** (~the full envelope) |
| 8 senders × 250 Hz (sized to demand) | 250.5/s each — all meet | 250.0–250.5/s each — all meet |
| flood + 250 Hz legitimate client | flood 271.3/s, legit 250.0/s, 0 throttled | flood 1,022.0/s, legit 250.3/s, 0 throttled |
| shared bucket (pre-fairness reference, §32b) | — | — (legit starved to 15.7/s — the mechanism exists to close this) |

Mode-independent guarantees (NPS-010 §7.1.1, v1.7.0): total intake is
capped at the shared envelope, and a legitimate client's share is
protected in both modes. The §32e numbers reproduce within noise on
re-run (flood 1,021.7 → 1,022.0; legit 250.3 → 250.3).

## Options and the brief's recommendation

| Option | What it means | Brief's recommendation |
|---|---|---|
| **A. Keep static default (shipped)** | New endpoints are fair-by-default with fixed shares; the operator may enable dynamic per endpoint (`dynamic_shares=True`, normative in NPS-010 §7.1.1 v1.7.0) | **Recommended.** The adversarial-optimal posture: static minimizes an abuser's absolute take (~271/s vs ~1,022/s under flood; ~313/s vs ~2,063/s lone-sender headroom) while delivering the identical legitimacy guarantee. Under-utilization — static's cost — is bounded, predictable, and operator-curable per endpoint with the knobs that already exist. |
| **B. Dynamic default** | New endpoints divide the envelope by the live sender count | Rejected on this data. It hands every low-occupancy endpoint's headroom to the loudest sender by default — the platform would need a policy judgment that the ~3.8× larger flooder take is acceptable platform-wide, and no current workload evidence argues for it. |
| **C. Class-based defaults** | Dynamic default for named endpoint classes (e.g. media/streaming), static otherwise | Viable later, but premature: it needs the per-class workload inventory the platform does not have yet (real Nyrqis app traffic shapes). Revisit with utilization data showing static shares actually starving known-good low-occupancy senders in practice. |

**Recommendation: Option A — no tree change.** The shipped posture is
the one the data supports; the decision would record that the standing
item is disposed with "static default retained, dynamic opt-in
normative," closing the ADR-0009 follow-on with nothing left open in
the rate-limiting space.

## Consequences ledger — what each option mechanically triggers

| Option | Immediate tree edits | Follow-up work |
|---|---|---|
| A (retain static) | `AG_AGENDA.md` decision log row; standing item marked disposed; ADR-0009 Implementation Note updated | none — the knobs, the guarantee, and the spec already ship |
| B (dynamic default) | `IPCManager` default flips (`default_dynamic_shares=True`); NPS-010 §7.1.1 amendment; ADR-0009 amendment; §32e/§32f re-run as the acceptance record | adversarial monitoring posture for low-occupancy endpoints; possible per-endpoint static opt-in knob (the inverse of today's) |
| C (class-based) | endpoint-class manifest schema; `configure_endpoint_rate_limit` class binding; NPS-010 amendment | per-class workload inventory (does not exist yet) |

## What this brief deliberately does not answer

1. The per-endpoint observation window a dynamic-mode operator should
   watch (an operational runbook question, not a Group policy one).
2. Whether future endpoint classes (media, input, AI) should carry
   different `fair_shares` defaults — a workload-data question that
   arrives with real Nyrqis application traffic.
3. Any change to the §7.1.1 normative guarantee — it is
   mode-independent and this brief does not propose touching it.

---

**End of Document**
