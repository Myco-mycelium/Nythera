---
title: Streaming Data Plane — Chunked Framing for Large CALL Payloads
document_id: ADR-0024
version: 0.1.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-16
updated: 2026-08-16
ai_assisted: true
depends_on: [NPS-003, NPS-011, ADR-0009, ADR-0020, ADR-0021, ADR-0022, ADR-0023]
---

> **Status (2026-08-16):** Proposed — drafted for Architecture Group
> review as the documented next step of ADR-0022's data plane. This ADR
> fixes the *wire-level* answer to the 32 KiB per-call payload cap: how
> a single CALL/REPLY carries more data than one 64 KiB datagram holds.
> It is a protocol decision, deliberately written BEFORE the
> implementation, exactly as the quota design was written up before
> 0.14.10 implemented it.
>
> **First increment IMPLEMENTED the same day (0.14.20) — service-level
> streaming, with the wire-level framing as the documented follow-on:**
> the streaming data plane now works end-to-end, but the chunk envelope
> rides ORDINARY capability-gated CALLs and reassembly lives at the
> storage service + client boundary rather than in the codec header +
> serving loop. Chunks are `volume_write`/`volume_read` CALLs with a
> `stream_id`/`stream_index`/`stream_count` envelope + per-chunk SHA-256;
> the service reassembles writes (bound to the first chunk's sender,
> ≤512 chunks / 30 s TTL, duplicate/mismatch/checksum failures reject
> the stream) and performs ONE write/quota-check/accounting/commit on
> the final chunk; streamed reads page through NyFS in-process and
> return correlated ≤32 KiB REPLY pieces the client collects by index.
> **This keeps the wire codec byte-identical (the differential gate
> stays green) and the Rust serving loop unchanged (chunks dispatch on
> either loop path)** — the tradeoff accepted for increment 1, exactly
> as ADR-0022's byte path landed before its streaming. The ADR's
> wire-level placement (a codec flag distinguishable from an ordinary
> CALL + Rust loop reassembly serving ALL services, and the Rust client
> half streaming) remains the follow-on increment. The §29 evidence run
> (`--vault-stream`) is in `tests/BENCHMARK_RESULTS.md`: 1 MiB writes
> 5.6× faster plaintext / 6.6× encrypted vs paging; reads ~1.04×
> (already flat — AEAD block decode dominates, and each piece still
> rides its own REPLY datagram).
>
> **Why now — the measured cost:** ADR-0022's live FUSE-mount benchmark
> (§27, `tests/BENCHMARK_RESULTS.md`) shows the per-CALL round trip
> dominates the data plane: a 1 MiB kernel write rides **32 sequential
> 32 KiB CALLs** (one datagram each) through the encrypted passthrough
> (~3.2 MB/s vs ~1,700 MB/s native), and the AEAD-per-32 KiB block cost
> is only part of it — the CALL round-trip and JSON/base64 envelope per
> 32 KiB chunk are the rest. Streaming is the levers that collapse the
> per-CALL overhead into a single dispatch for the whole kernel
> request.
>
> **Scope boundary (honest):** this ADR does NOT change the NyFS block
> layer (64 KiB blocks, AEAD per block — ADR-0023), the datagram
> transport itself (NPS-003 §4.3, ADR-0021's loop), or the datagram
> budget of ordinary calls. It adds ONE new wire capability: a CALL
> (or REPLY) whose payload exceeds the single-datagram budget is split
> into chunks that the receiver reassembles before dispatch. Everything
> ≤ the budget is byte-identical to today. Back-compat is first-class.

# ADR-0024 — Streaming Data Plane: Chunked Framing for Large CALL Payloads

## Context

ADR-0022's storage service and its FUSE passthrough (`fuse/vault_mount.py`)
move bytes through the IPC transport one CALL at a time. The transport
is Unix-domain **datagrams** (NPS-003 §4.3) with 64 KiB socket buffers
(CALL and REPLY alike), so a single volume op must stay under that once
JSON + base64 are accounted for. The service therefore caps every byte
payload at **32 KiB** (`_MAX_IO_BYTES` in `ipc/storage.py`), and the
passthrough **pages** — a kernel read/write of `N` bytes becomes
`ceil(N / 32 KiB)` sequential CALLs, each with its own round trip,
envelope, and dispatch.

The measured cost of paging (BENCHMARK_RESULTS.md §27, 2026-08-15):

| Pattern | Encrypted FUSE mount | Native | Δ |
|---------|---------------------|-------|-----|
| Write, 1 MiB syscalls | 3.25–3.40 MB/s | 975–1,738 MB/s | ~400× |
| Write, 4 KiB syscalls | 0.77–0.80 MB/s | 985–1,605 MB/s | ~1,500× |
| Read, 1 MiB | 2.17 MB/s | 3,079–4,232 MB/s | ~1,600× |
| Read, 4 KiB | 2.03–2.16 MB/s | 985–1,200 MB/s | ~500× |

The 1 MiB patterns are the paging cost made visible: 32 round trips
for one logical write. The 4 KiB patterns show the per-CALL floor even
when paging is not the issue. The write side already moved the durable
commit off the per-CALL path (0.14.8 batching, 0.14.9 group commit);
the remaining gap is the CALL itself.

Design constraints inherited from earlier decisions:

- **Receiver-side kernel identity stays the trust anchor** (NPS-003
  §4.3, ADR-0021): the sender attaches nothing; `SO_PASSCRED` supplies
  `(pid, uid, gid)` on every inbound datagram. Any streaming framing
  must keep authenticating **per datagram**, not per logical call.
- **The datagram budget stays 64 KiB** (ADR-0022): chunks must fit one
  datagram with their framing overhead.
- **The serving loop stays behind the FFI boundary** (ADR-0020/0021):
  reassembly is a wire-layer concern, so it belongs where the loop
  lives — not in the storage service.
- **Rate limiting stays per-sender** (ADR-0009): a stream must not
  let a sender bypass its token bucket by splitting one large call.

## Decision

**A CALL or REPLY whose payload exceeds the single-datagram budget is
transmitted as a bounded, checksummed, ordered sequence of chunks and
reassembled by the receiver before dispatch.** Small calls (the
overwhelming majority — status, control, metadata ops) never change:
their wire bytes are identical to today.

Specifically:

- **Chunk size.** `CHUNK = 32 KiB` of payload per chunk (the current
  `_MAX_IO_BYTES`, so the framing envelope keeps a chunk + JSON/base64
  inside the 64 KiB datagram budget with headroom). Payloads ≤ 32 KiB
  remain a single ordinary CALL/REPLY — **no framing, byte-identical
  wire** (back-compat: an old client and a new daemon interoperate in
  both directions for every ≤-budget call).
- **Stream header (the only new wire element).** A stream is
  identified by a fresh **stream_id** (CSPRNG, 48-bit — the same
  generator as message ids) bound to the sender's kernel identity at
  the receiver. Every chunk datagram carries a small binary prefix:
  `stream_id ‖ chunk_index (u32) ‖ chunk_count (u32) ‖ payload_len
  (u32) ‖ payload ‖ checksum`. The message `call_id`/`reply_id`
  correlation rides the *last* chunk's envelope, so the existing
  correlation machinery is untouched. The prefix is distinguishable
  from an ordinary CALL by a flag bit in the existing codec header
  (migration #4's `rust/ipc` framing), so a receiver can reject a
  stream header it does not understand instead of mis-parsing it.
- **Reassembly is receiver-side and bounded.** The receiver buffers
  chunks keyed by `(kernel-attached sender, stream_id)` — never by a
  claimed sender — and dispatches the reassembled CALL only when the
  last chunk arrives and the per-chunk checksums verify (the SHA-256
  primitive already exists behind the boundary in `rust/nyfs`,
  ADR-0020 migration #3). A stream is subject to:
  - **an in-flight window** (at most `W` chunks buffered per stream —
    the sender paces itself on the window), and
  - **a reassembly TTL** (an incomplete stream is dropped after the
    timeout, and its buffers reclaimed).
  Both bound memory to `W × CHUNK × concurrent streams`, independent
  of total transfer size. Reassembly never allocates more than the
  reassembled payload size.
- **Flow control and rate limiting compose.** The existing per-sender
  token bucket (ADR-0009) still meters every chunk datagram — a stream
  cannot amortize its way past the bucket. The window paces the
  stream; the bucket paces the sender. A stream that exceeds either is
  dropped fail-closed (the CALL fails; no partial state).
- **Where it lands.** Reassembly lives in the wire layer that the
  serving loop already owns: the floor (`ipc/transport.py` +
  `ipc/transport_codec.py`) and, when the crate is present, the Rust
  half (migration #6 `rust/transport` + migration #4 `rust/ipc`
  framing) behind the FFI boundary. Services see the reassembled CALL
  exactly as they do today — `volume_write` gets its full payload in
  one dispatch. The passthrough's paging loop (`fuse/vault_mount.py`)
  collapses to one streaming CALL per kernel request; the storage
  service's `_MAX_IO_BYTES` cap lifts to the stream budget (a config
  bound, not a protocol one).
- **Replies stream the same way.** A large REPLY (e.g. a big
  `volume_read`) uses the identical chunk framing back to the caller,
  with the same window/TTL bounds.

## Alternatives considered

- **Keep paging (status quo).** The 32-CALL-per-MiB pattern of §27
  stays. Rejected as the *decision*: the round-trip and envelope costs
  are measured and structural — paging is the single largest
  contributor to the ~400–1,600× gap. It remains the fallback for
  peers that do not support streaming.
- **Raise the datagram buffers** (e.g. 1 MiB datagrams). Rejected:
  it pushes the same memory bound into every socket's kernel buffers,
  scales with the largest call instead of a fixed window, and the
  JSON/base64 envelope still wastes ~1/3 of each datagram. It also
  does nothing for flow control — a misbehaving sender allocates
  unbounded kernel memory per datagram.
- **Switch the transport to stream (SOCK_STREAM) sockets for large
  calls.** Rejected: it splits the trust model (streams carry no
  per-datagram `SCM_CREDENTIALS`; `SO_PEERCRED` is connect-time only,
  and the current probe shows it does not even work for datagrams
  here), contradicts NPS-003 §4.3's datagram model, and forks ADR-0021's
  single loop into two serving paths. The chunked-datagram framing
  keeps one transport, one auth model, one loop.
- **Shared memory for the payload.** Rejected for this increment: it
  adds a second IPC mechanism, per-sender buffer management, and
  lifetime/revocation complexity for a benefit (copy elimination) that
  the FUSE passthrough's own copy into a kernel page already swamps.
  It may return later as an optimization behind the same wire decision.

## Consequences

- **Performance:** a 1 MiB kernel write becomes ONE CALL of 32 chunks
  with one dispatch instead of 32 CALLs. The AEAD-per-32 KiB block
  cost (ADR-0023) remains — the block layer is untouched — but the
  per-CALL round-trip and envelope overhead collapse. Expected: the
  encrypted-mount write/read rates move up by roughly the paging
  factor on multi-chunk I/O (to be measured as §29, `--vault-stream`,
  before this ADR is accepted — the same evidence-first rule
  ADR-0021 used).
- **Memory:** bounded by `W × CHUNK × concurrent streams` +
  reassembly TTL, independent of payload size. A sender cannot force
  unbounded allocation by claiming a huge stream; the window and TTL
  are the same kind of bound the token bucket already provides.
- **Security:** identity is still kernel-attached per datagram. The
  receiver binds a stream to the sender of its first chunk; chunks
  from a different sender fail the bind and are dropped. Checksums
  verify before dispatch. No new capability; a stream is not a
  privilege — any caller allowed a byte op may stream it.
- **Back-compat:** calls ≤ 32 KiB are byte-identical (the 
  overwhelming majority of traffic). Old clients and new daemons
  interoperate; a stream-header flag a peer does not understand is
  rejected with a clear error, and the caller falls back to paging —
  which stays implemented forever as the degradation path.
- **ADR-0022/0023 surface:** `volume_write`/`volume_read` serve
  payloads beyond `_MAX_IO_BYTES` (the cap becomes a config bound for
  the stream path); the passthrough paging loop simplifies. The quota
  and accounting layers are unaffected — they bill on committed bytes,
  not CALL shape.
- **NPS impact:** NPS-003 §3 gains the streaming-framing section when
  the platform spec is next touched (the codec is migration #4's
  `rust/ipc` framing — its header gains the stream flag). No
  renumbering (ADR-0017).

## References

- NPS-003 §3 (CALL/REPLY), §4.3 (datagram transport, receiver-side
  kernel identity), §6.1 (latency gate)
- ADR-0009 (per-container token-bucket rate limiting)
- ADR-0020 (languages + platform boundary; migrations #3/#4/#6)
- ADR-0021 (NyRuntime serving loop behind the FFI boundary)
- ADR-0022 (NyVault storage service; the 32 KiB per-call cap this ADR
  replaces on the stream path)
- ADR-0023 (NyVault key manager; AEAD block layer stays per-block)
- `tests/BENCHMARK_RESULTS.md` §27 (the measured paging cost),
  §29 (to be: `--vault-stream` evidence run)
