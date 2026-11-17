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

Six cells, in this order, all of them run from ring 3 at the end of
`userprog/prog_svc84.s`, after the program has detached its own display slot
and given back every surface tile it opened:

| # | Call | Row | Bad argument | Expected | Verdict byte |
|---|---|---|---|---|---|
| 0 | `0x42` | `GFX_MODE_INTO` | `a0 = 0x1001`: misaligned **and** not user-accessible | `-EFAULT` | `0xf2` |
| 1 | `0x5a` | `CAP_BLOCK_INFO` | `a0 = 31`: forged block handle, record pointer real | `-EBADF` | `0xf7` |
| 2 | `0x5c` | `CAP_BLOCK_CACHE_STATS` | `a0 = 31`: forged handle, record pointer real | `-EBADF` | `0xf7` |
| 3 | `0x5b` | `CAP_BLOCK_WRITE` | `a0 = 31`: forged handle, buffer and length legal | `-EBADF` | `0xf7` |
| 4 | `0x66` | `DISPLAY_DETACH` | nothing to detach: its slot is already back | `-EACCES` | `0xf3` |
| 5 | `0x6c` | `DISPLAY_SURFACE_RELEASE` | no tile left: the churn gave them all back | `-ENOENT` | `0xfe` |

The kernel packs those bytes into one word in cell order, low byte first, and
prints it:

```
service-0x84 proof: REFUSAL-MATRIX (rows 0x6 , matched 0x6 , packed 0xfef3f7f7f7f2) , REFUSAL-MATRIX matches
```

`0xfef3f7f7f7f2` is the whole matrix at a glance. A cell that starts being
served instead of refused changes its byte, the gate's exact-string assertion
fails, and the kernel prints a `REFUSAL-MATRIX ROW 0x…` line naming the row, the
bad argument and both values. Rows 4 and 5 are the reason the matrix is worth
having: two "nothing to give back" rows, two different names (`EACCES` on the
slot, `ENOENT` on the tile), and nothing but a per-row sweep would catch them
being swapped.

## The two rules a cell has to obey

**1. The row must validate before it acts.** A probe that makes a row act and
then refuse mutates the state other legs read back. This is not hypothetical:
the first attempt put `0x4f CAP_SHM_MAP` in the matrix with a length that was
not 4096-aligned, expecting `-EINVAL`. That row *aligns* the length and maps —
it consumed frames and clobbered live region slots, and three unrelated legs
(GFX/INPUT readback, MAP, SPACES) went red at once.

**2. The answers live in their own page.** `REFUSAL_MATRIX_SLOT` is 12288, the
first byte of a **fourth** `PROOF_BUF` page that exists only because the matrix
does (`svc84_proof.rs` maps four pages, not three). The first attempt used
12160, inside the third page; that is the ring-3 stack the probes are called
from, and the same three legs failed a second time for a second reason. Probes
now write at `BUF+12288+8i` and keep their argument buffers at `BUF+12416` and
above, so no slot or record overlaps a live result or the stack.

## What this found

- **`0x42 GFX_MODE_INTO` does not refuse a misaligned pointer.**
  `memory::paging::user_range_ok` walks mappings and checks
  `PRESENT|USER_ACCESSIBLE|WRITABLE`; it never looks at alignment, and the row
  uses `write_unaligned`. A misaligned *mapped* pointer is therefore served, and
  8 bytes land at an odd address. The matrix cell pins the refusal that is real
  (`-EFAULT` for a non-user page) and this note records the other half, rather
  than blessing an expectation the kernel does not hold.
- **`0x4f CAP_SHM_MAP` aligns rather than refuses** a non-page-aligned length.
  Measured, not assumed — it is what broke the first attempt. Either the doc row
  or the row itself is wrong about that; the matrix cannot hold a cell for it
  until one of them changes.

## Adding a cell

1. Append a `RefusalRow { call, want, what }` to `REFUSAL_MATRIX` in
   `src/service84.rs` and bump its length.
2. Append the probe to the matrix block in `userprog/prog_svc84.s`, writing its
   answer at `BUF+12288+8*n` and keeping any argument record at `BUF+12416+`
   or higher.
3. Update the packed word and row count in the `REFUSAL-MATRIX` assertion in
   `tools/svc84_proof_test.sh`.
4. Run `./tools/svc84_proof_test.sh` for **that cell alone** before adding the
   next one. A batch hides which cell moved; one cell per run makes the `ROW`
   line attributable.
5. If the cell fails, decide which side is wrong — the row or the doc — before
   changing the expectation. A cell corrected to match the code without that
   decision is how a proof starts agreeing with its bugs.

## What is still not swept

43 rows; 6 cells. The remaining rows are covered by the legs that exercise
them, and those legs do assert refusals inline — forged handles (`-EBADF`),
out-of-window or wrong-kind accesses (`-EACCES`), zero or oversized lengths
(`-EINVAL`), unmapped releases (`-ENOENT`). What no leg does is sweep each row
with each class of malformed argument, which is what a full matrix would be:

- `0x43 PROOF_DONE`, `0x4e CAP_EXIT`: never return to the caller, so a refusal
  cell does not exist for them in the same sense.
- Rows that allocate, map, queue or write before they finish validating
  (`0x4f`, the region rows, `0x45`/`0x46` under load): a probe for these needs
  its own process, so that a mutation is contained by that process's exit
  rather than by the proof continuing.
- The rest: safe in principle, simply not written yet. Each one is a
  `RefusalRow` and about ten instructions of assembly.
