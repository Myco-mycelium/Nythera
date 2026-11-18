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

Twenty-five cells, in this order, all of them run from ring 3 at the end of
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
| 13 | `0x59` | `CAP_BLOCK_READ` | `a0 = 2^32`: handle above `u32::MAX` | `-EBADF` | `0xf7` |
| 14 | `0x5a` | `CAP_BLOCK_INFO` | `a0 = 2^32`: handle above `u32::MAX` | `-EBADF` | `0xf7` |
| 15 | `0x5b` | `CAP_BLOCK_WRITE` | `a0 = 2^32`: handle above `u32::MAX`, rest of the call legal | `-EBADF` | `0xf7` |
| 16 | `0x5c` | `CAP_BLOCK_CACHE_STATS` | `a0 = 2^32`: handle above `u32::MAX` | `-EBADF` | `0xf7` |
| 17 | `0x59` | `CAP_BLOCK_READ` | `a_xfer = 16385`: one byte past `MAX_BLOCK_SPAN` | `-EINVAL` | `0xea` |
| 18 | `0x4f` | `CAP_SHM_MAP` | `a0 = 69632`: 17 pages, one past `MAX_REGION_REQUEST_PAGES` | `-EINVAL` | `0xea` |
| 19 | `0x59` | `CAP_BLOCK_READ` | `a2 = BUF+20480`: the read-only page, real handle, LBA and length | `-EFAULT` | `0xf2` |
| 20 | `0x4f` | `CAP_SHM_MAP` | `a1 = BUF+20480`: the read-only page, legal one-page length | `-EFAULT` | `0xf2` |
| 21 | `0x4c` | `CAP_SHM` | `a0 = 2^32`: handle above `u32::MAX`, record real | `-EBADF` | `0xf7` |
| 22 | `0x4d` | `CAP_SHM_CLOSE` | `a0 = 2^32`: handle above `u32::MAX`, nothing else claimed | `-EBADF` | `0xf7` |
| 23 | `0x50` | `CAP_SHM_UNMAP` | `a0 = 2^32`: handle above `u32::MAX`, nothing else claimed | `-EBADF` | `0xf7` |
| 24 | `0x52` | `CAP_NOTIFY` | `a0 = 2^32`: handle above `u32::MAX`, zero signal bits | `-EBADF` | `0xf7` |

The kernel folds those bytes, in cell order, into ONE word with FNV-1a and
prints it:

```
service-0x84 proof: REFUSAL-MATRIX (rows 0x19 , matched 0x19 , fold 0x885f33b79cbeebe9) , REFUSAL-MATRIX matches
```

The fold replaced a byte-per-cell layout, not because the bytes were wrong but
because the layout needed to know where a cell lands — a shift, then a word
index, then an eight-cell stride — and each of those broke once (see below).
FNV-1a needs none of it: no shift, no word count, no limit, and `wrapping_mul`
cannot trap. The fold is a diagnostic, not the verdict: a disagreement sets
`matrix_ok` false, is counted in `matched`, and is printed as a
`REFUSAL-MATRIX ROW 0x…` line naming the row, the bad argument and both values.
The gate pins the fold as one exact string, so any byte changing in any cell
fails the boot.

## The argument classes, and what each one costs

| Class | Cells | Why it is a refusal |
|---|---|---|
| forged handle | `0x5a`, `0x5b`, `0x5c` | the handle resolves before any record is written or sector staged |
| zero / illegal length | `0x59`, `0x5b`, `0x4f` (×2), `0x4f` (alignment) | the shape check is the first thing each row does; nothing is allocated or read |
| misaligned, non-user pointer | `0x42`, `0x4f`, `0x4c`, `0x5a` | the range check runs before the mapping source, the handle lookup or the record write |
| absent state | `0x66`, `0x6c` | no slot, no tile: two "nothing to give back" rows with two different names (`EACCES`, `ENOENT`) |
| handle above `u32::MAX` | `0x59`, `0x5a`, `0x5b`, `0x5c`, `0x4c`, `0x4d`, `0x50`, `0x52` | the `EBADF` arm is each handle row's FIRST check, before the record, the length or authority — a handle that cannot be named is never truncated into one that can. `0x51 CAP_BADGE` looks similar but answers `EINVAL` for an oversized `a0` (its shape check owns `a0` and `a1` together), so it is deliberately NOT in this class |
| oversized length | `0x59`, `0x4f` | each row's own cap (`MAX_BLOCK_SPAN`, `MAX_REGION_REQUEST_PAGES`) is checked with the zero and alignment checks, so too much is refused before a sector is read or a frame allocated |
| read-only destination | `0x59`, `0x4f` | the out-pointer is validated for **writing**, so a page that is present and user-accessible with its write bit clear is refused before a sector is read or a region allocated |

Cells 4 and 5 are why a matrix beats a checklist: swapping those two errnos
would look right row by row and fail here. Cells 1 and 12 are the same row
(`0x5a`) with two different bad arguments, answering `EBADF` when the record is
real and `EFAULT` when it is not — which is only true because the row validates
the record *before* it resolves the handle, and the matrix is what holds it to
that order. Cells 19 and 20 are the same idea on the write side: one
block row and one region row, both handed the one page a process may read but
may not write, and both answering `-EFAULT` for that reason alone. Cells 21–24 extend the handle
class beyond the four block rows: `0x4c` (geometry), `0x4d` (close), `0x50`
(window drop) and `0x52` (notify) each open with the same guard, and each
cell pins it against a distinct answer the row could otherwise give — the
table's `EACCES`/`ENOENT`, or a truncation to handle 0, which is a real
capability (the endpoint owner's).

## The two rules a cell has to obey

**1. The row must validate before it acts.** A probe that makes a row act and
then refuse mutates the state other legs read back. The first attempt at this
matrix blamed `CAP_SHM_MAP (0x4f)` for four red legs ("it aligns the length and
maps"). That was wrong, and reading `serve_region_request` showed it: `a0 % 4096`
is checked before the mapping source is asked. The red legs were rule 2 below.
The blame cost a round trip; `0x4f` has four cells today, all of them refused
before a frame is allocated.

**2. The answers live in their own page.** `REFUSAL_MATRIX_SLOT` is 12288, the
first byte of a **fourth** `PROOF_BUF` page that exists only because the matrix
does (`svc84_proof.rs` maps four pages, not three). The first attempt used
12160, inside the third page; that is the ring-3 stack the probes are called
from, and the same four legs failed a second time for a second reason. Probes
write at `BUF+12288+8i` and keep argument records at `BUF+12800` and above —
the records lived at 12416 until the seventeenth cell's slot needed that byte,
and they moved in the same change rather than sharing it.

**3. The write side needs a page that cannot be written.** A non-user pointer
is refused by the read check and the write check alike, so every earlier
pointer cell proved neither. The write-side cells need a page that is
**USER_ACCESSIBLE and not WRITABLE**, and `svc84_proof.rs` maps exactly one:
`PROOF_RO` at `CODE+0x1_5000`, a fifth page, mapped with its own flag set (not
the results pages' `flags`) so a later edit to those cannot quietly make it
writable and turn both cells into successes. Nothing ever writes through it;
the cells are EFAULT only because the rows asked for a WRITABLE range, and a
row that passed `false` or skipped the check would serve the call and fault
ring 3 on its own copy.

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
- **Both destination checks ask for a writable range.** `CAP_BLOCK_READ` and
  `CAP_SHM_MAP` answered `-EFAULT` for the read-only page on their first run
  (cells 19 and 20), which is the expected result but not a mutation: the cells
  pin the behaviour, and `gate_selfcheck.sh`'s fold mutation is what proves a
  changed byte would fail the gate. What the cells would catch is a row that
  stopped asking for WRITABLE — then the call would be served and ring 3 would
  fault inside the copy, taking the proof down with it rather than answering a
  number.
- **The non-block rows guard the handle space themselves.** `0x4c`, `0x4d`,
  `0x50` and `0x52` each answered `-EBADF` for `a0 = 2^32` on their first run.
  Without that guard `a0 as u32` is `0`, a live capability in the proof's own
  table, so the cells are what separate "refused before the table" from
  "answered for some other handle entirely". `0x51 CAP_BADGE` is the one
  badge-family row that answers `EINVAL` here: its check owns `a0` and `a1`
  together, so it belongs to the shape class, not this one, and moving it
  into this class would be changing the row to match the matrix.
- **The projection broke twice before it was a fold.** Packed words are
  positional, and every positional version had to know where a cell lands. One
  word panicked the kernel mid-verdict at the ninth cell (`attempt to shift
  left with overflow`, `service84.rs:2315`), taking every leg down; the fix
  was a second word. Then the SEVENTEENTH cell wrapped `index % 8` onto the
  ninth's byte (`0xea | 0xf7` = `0xff`) while every cell still printed
  `matched 0x11` — only the gate's exact-string assertion caught it, because
  `matched` and the fold are two independent descriptions of the same run.
  Three hand-named words would have been the third version of the same
  mistake, so the layout went: FNV-1a over the bytes is one word, needs no
  position, and cannot overflow at any cell count.

## Adding a cell

1. Append a `RefusalRow { call, want, what }` to `REFUSAL_MATRIX` in
   `src/service84.rs` and bump its length.
2. Append the probe to the matrix block in `userprog/prog_svc84.s`, writing its
   answer at `BUF+12288+8*n` (the next free answer slot) and keeping any
   argument record at `BUF+12800` or higher.
3. Fold the new answer byte into the expected word — FNV-1a 64 over the low
   byte of every answer in cell order — and update the row count and fold in
   the `REFUSAL-MATRIX` assertion in `tools/svc84_proof_test.sh`, and the
   mutation string in `tools/gate_selfcheck.sh` (its `refusal-matrix cell` case
   corrupts the fold, so keep the new word in step or the meta-gate fails).
4. Run `./tools/svc84_proof_test.sh` for **that cell alone** before adding the
   next one. A batch hides which cell moved; one cell per run makes the `ROW`
   line attributable.
5. If the cell fails, decide which side is wrong — the row or the doc — before
   changing the expectation. A cell corrected to match the code without that
   decision is how a proof starts agreeing with its bugs.

## What is still not swept

43 rows; 25 cells. The remaining rows are covered by the legs that exercise
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
  shape is settled first, which is why it has four cells.
- The **forged**-handle arm of the non-block rows (`0x4c`, `0x4d`, `0x50`,
  `0x52`): a handle *inside* the u32 space that names nothing (`a0 = 31`)
  answers from the capability table, not from the row's guard, so it is a
  different cell from the four above even though the errno is the same. It is
  safe to probe (the table lookup precedes any write) and simply not written
  yet.
- The remaining badge rows (`0x51` badge, `0x53` take): `0x51` has no `EBADF`
  arm at all — its oversized handle is `EINVAL` — and `0x53`'s handle check
  has not been read closely enough to claim a cell, which is why neither is
  in the table.
- The rest: safe in principle, simply not written yet. Each one is a
  `RefusalRow` and about ten instructions of assembly.
