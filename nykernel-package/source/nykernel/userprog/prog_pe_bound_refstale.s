/*
 * prog_pe_bound_refstale.s -- the boundary-import increment's
 * SECOND control carrier (the stale REF case): a real
 * native-subsystem PE32+ executable whose ONLY import is the forwarder
 * host's crossfn_fwd, byte-for-byte the sibling of
 * prog_pe_bound_bad.s. Its entry point is never entered.
 *
 * tools/patch_pe_bound.py composes into it a bound-import directory
 * whose single entry is IDENTICAL to the bad client's except ONE
 * composed u32:
 *   (a) declares the FORWARDER HOST'S OWN .edata TimeDateStamp (a
 *       fresh descriptor, exactly like the bad client),
 *   (b) carries ONE forwarder ref declaring the PRIMARY PROVIDER's
 *       stamp PLUS ONE -- a stamp NO registered file carries: the
 *       chasing target "was rebuilt" since the client was bound,
 *   (c) fills its single IAT slot with the SAME deliberately WRONG
 *       value the bad client carries.
 * The ruling's gate flips OFF by the ref alone (the descriptor's own
 * stamp still matches): the same wrong slot must NOT honor and NOT
 * refuse -- it is DISCARDED (typed StaleRefDiscarded) and the slot is
 * resolved fresh through the forwarder chase, landing exactly where
 * the bad client's fresh resolution lands (the primary provider's
 * crossfn). The bad/refstale pair is the demonstration that ONE stamp
 * byte decides refuse-vs-discard; this file's resolution SUCCEEDS
 * where the bad client refuses. See tools/patch_pe_bound.py.
 */
    .text
    .global _start
_start:
    mov __imp_crossfn_fwd(%rip), %rax
    int $0x8D
.hang:
    pause
    jmp .hang
