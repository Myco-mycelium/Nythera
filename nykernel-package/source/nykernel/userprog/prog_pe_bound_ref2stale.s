/*
 * prog_pe_bound_ref2stale.s -- the forwarder-REF increment's
 * multi-hop SECOND carrier (TWO refs, the SECOND one stale): a real
 * native-subsystem PE32+ executable whose ONLY import is the forwarder
 * host's crossfn_fwd, byte-for-byte the sibling of
 * prog_pe_bound_ref2bad.s. Its entry point is never entered.
 *
 * tools/patch_pe_bound.py composes into it a bound-import directory
 * IDENTICAL to the ref2bad carrier's except ONE composed u32:
 *   (a) the same fresh descriptor declaring the FORWARDER HOST'S OWN
 *       .edata TimeDateStamp,
 *   (b) the same two forwarder refs -- the first (the PRIMARY
 *       PROVIDER) still declaring its own fresh stamp, but the SECOND
 *       (the forwarder host itself) declaring the host's own stamp
 *       PLUS ONE: a stamp NO registered file carries,
 *   (c) the same single, deliberately WRONG bound IAT value.
 * The gate flips OFF by the SECOND ref alone: the same wrong slot
 * must NOT honor and NOT refuse -- it is DISCARDED (typed
 * StaleRefDiscarded) and resolved fresh through the same forwarder
 * chase, landing exactly where the ref2bad carrier's refusal named.
 * One LATER-ref stamp byte decides refuse-vs-discard across a TWO-ref
 * chain. See tools/patch_pe_bound.py.
 */
    .text
    .global _start
_start:
    mov __imp_crossfn_fwd(%rip), %rax
    int $0x8D
.hang:
    pause
    jmp .hang
