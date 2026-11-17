# The per-row refusal matrix

`service-0x84` is served by 43 call numbers. The proof's legs prove rows by
making them **succeed** and reading the answer back out of ring-3 memory; the
gate then pins the answers, the distinct errno kinds (5) and 27 distinct legs.
That left the blind spot the README used to name: a row can answer every
well-formed call perfectly and still mis-handle the malformed one, and no leg
would notice.

The refusal matrix is the other half. For a row it names, ring 3 makes a call
that **must be refused**, and the kernel compares the errno that comes back
against what that row's own documented rule says it should be.

## What is probed

Thirteen cells, in this order, all of them run from ring 3 at the end of
`userprog/prog_svc84.s`, after the program has detached its own display slot
and given back every surface tile it opened:

| # | Call | Row | Bad argument | Expected | Byte |
|---|---|---|---|---|---|
| 0 | `0x42` | `GFX_MODE_INTO` | `a0 = 0x1001`: misaligned **and** not user-accessible | `-EFAULT` | `0xf2` |
| 1 | `0x5a` | `CAP_BLOCK_INFO` | `a0 = 31`: forged block handle, record pointer real | `-EBADF` | `0xf7` |
| 2 | `0x5c` | `CAP_BLOCK_CACHE_STATS` | `a0 = 31`: forged handle, record pointer real | `-EBADF` | `0xf7` |
| 3 | `0x5b` | `CAP_BLOCK_WRITE` | `a0 = 31`: forged handle, buffer and length legal | `-EBADF` | `0xf7` |
| 4 | `0x66` | `DISPLAY_DETACH` | nothing to detach: its slot is already back | `-EACCES` | `0xf3` |
| 5 | `0x6c` | `DISPLAY_SURFACE_RELEASE` | no tile left: the churn gave them all back | `-ENOENT` | `0xfe` |
| 6 | `0x4f` | `CAP_SHM_MAP` | `a0 = 100`: length in bytes, not a multiple of the page size | `-EINVAL` | `0xea` |
| 7 | `0x59` | `CAP_BLOCK_READ` | transfer length `0` | `-EINVAL` | `0xea` |
| 8 | `0x5b` | `CAP_BLOCK_WRITE` | transfer length `0` | `-EINVAL` | `0xea` |
| 9 | `0x4f` | `CAP_SHM_MAP` | length `0` | `-EINVAL` | `0xea` |
| 10 | `0x4f` | `CAP_SHM_MAP` | `a1 = 0x1001`: misaligned, non-user record pointer, legal length | `-EFAULT` | `0xf2` |
| 11 | `0x4c` | `CAP_SHM` | `a1 = 0x1001`: misaligned, non-user record pointer, forged handle | `-EFAULT` | `0xf2` |
| 12 | `0x5a` | `CAP_BLOCK_INFO` | `a1 = 0x1001`: misaligned, non-user record pointer, forged handle | `-EFAULT` | `0xf2` |

The kernel packs those bytes, eight per word little-endian, and prints both
words:

```
service-0x84 proof: REFUSAL-MATRIX (rows 0xd , matched 0xd , packed 0xeaeafef3f7f7f7f2/0xf2f2f2eaea) , REFUSAL-MATRIX matches
```

That pair is the whole matrix at a glance: bytes 0–7 in the first word, 8–12 in
the second. A cell that starts being served instead of refused changes its
byte, the gate's exact-string assertion fails, and the kernel prints a
`REFUSAL-MATRIX ROW 0x…` line naming the row, the bad argument and both values.

## The argument classes, and what each one costs

| Class | Cells | Why it is a refusal |
|---|---|---|
| forged handle | `0x5a`, `0x5b`, `0x5c` | the handle resolves before any record is written or sector staged |
| zero / illegal length | `0x59`, `0x5b`, `0x4f` (×2), `0x4f` (alignment) | the shape check is the first thing each row does; nothing is allocated or read |
| misaligned, non-user pointer | `0x42`, `0x4f`, `0x4c`, `0x5a` | the range check runs before the mapping source, the handle lookup or the record write |
| absent state | `0x66`, `0x6c` | no slot, no tile: two "nothing to give back" rows with two different names (`EACCES`, `ENOENT`) |

Cells 4 and 5 are why a matrix beats a checklist: swapping those two errnos
would look right row by row and fail here. Cells 1 and 12 are the same row
(`0x5a`) with two different bad arguments, answering `EBADF` when the record is
real and `EFAULT` when it is not — which is only true because the row validates
the record *before* it resolves the handle, and the matrix is what holds it to
that order.

## The two rules a cell has to obey

**1. The row must validate before it acts.** A probe that makes a row act and
then refuse mutates the state other legs read back. The first attempt at this
matrix blamed `CAP_SHM_MAP (0x4f)` for four red legs ("it aligns the length and
maps"). That was wrong, and reading `serve_region_request` showed it: `a0 % 4096`
is checked before the mapping source is asked. The red legs were rule 2 below.
The blame cost a round trip; `0x4f` has three cells today, all of them refused
before a frame is allocated.

**2. The answers live in their own page.** `REFUSAL_MATRIX_SLOT` is 12288, the
first byte of a **fourth** `PROOF_BUF` page that exists only because the matrix
does (`svc84_proof.rs` maps four pages, not three). The first attempt used
12160, inside the third page; that is the ring-3 stack the probes are called
from, and the same four legs failed a second time for a second reason. Probes
write at `BUF+12288+8i` and keep argument buffers at `BUF+12416` and above.

## What this found

- **`0x42 GFX_MODE_INTO` does not refuse a misaligned pointer.**
  `memory::paging::user_range_ok` walks mappings and checks
  `PRESENT|USER_ACCESSIBLE|WRITABLE`; it never looks at alignment, and the row
  uses `write_unaligned`. A misaligned *mapped* pointer is therefore served and
  8 bytes land at an odd address. Cells 0, 10, 11 and 12 pin the refusal that
  is real (`-EFAULT` for a non-user page) and say so, rather than implying the
  kernel validates alignment.
- **`0x4f` refuses a non-page-aligned length with `-EINVAL`** (cell 6), and the
  earlier claim that it aligned the length was the matrix's own bug being
  blamed on a row.
- **The pack word was a limit, not just a diagnostic.** The ninth cell shifted
  a byte by 64 bits and the kernel panicked mid-verdict (`attempt to shift left
  with overflow`, `service84.rs:2315`), taking every leg down with it. The pack
  is now two words and the cell count is unbounded; a matrix that silently caps
  at eight rows would be the blind spot it replaces wearing a new name.

## Adding a cell

1. Append a `RefusalRow { call, want, what }` to `REFUSAL_MATRIX` in
   `src/service84.rs` and bump its length.
2. Append the probe to the matrix block in `userprog/prog_svc84.s`, writing its
   answer at `BUF+12288+8*n` and keeping any argument record at `BUF+12416+`
   or higher.
3. Update the packed words and row count in the `REFUSAL-MATRIX` assertion in
   `tools/svc84_proof_test.sh`, and the mutation string in
   `tools/gate_selfcheck.sh` (its `refusal-matrix cell` case corrupts byte 0, so
   keep the new word in step or the meta-gate fails).
4. Run `./tools/svc84_proof_test.sh` for **that cell alone** before adding the
   next one. A batch hides which cell moved; one cell per run makes the `ROW`
   line attributable.
5. If the cell fails, decide which side is wrong — the row or the doc — before
   changing the expectation. A cell corrected to match the code without that
   decision is how a proof starts agreeing with its bugs.

## What is still not swept

43 rows; 13 cells. The remaining rows are covered by the legs that exercise
them, and those legs do assert refusals inline — forged handles (`-EBADF`),
out-of-window or wrong-kind accesses (`-EACCES`), zero or oversized lengths
(`-EINVAL`), unmapped releases (`-ENOENT`). What no leg does is sweep each row
with each class of malformed argument, which is what a full matrix would be:

- `0x43 PROOF_DONE`, `0x4e CAP_EXIT`: never return to the caller, so a refusal
  cell does not exist for them in the same sense.
- Rows that allocate, map, queue or write before they finish validating (the
  region rows behind `0x4f`, `0x45`/`0x46` under load): a probe for these needs
  its own process, so that a mutation is contained by that process's exit
  rather than by the proof continuing. `0x4f` itself is not one of them — its
  shape is settled first, which is why it has three cells.
- Classes not yet probed anywhere, worth naming so they are not mistaken for
  coverage: a **write** to a read-only or kernel page (every row's `-EFAULT`
  arm has a write side), a **handle above `u32::MAX`** (the `EBADF` arm each
  block row starts with), and an **oversized** length past the row's own cap.
- The rest: safe in principle, simply not written yet. Each one is a
  `RefusalRow` and about ten instructions of assembly.
