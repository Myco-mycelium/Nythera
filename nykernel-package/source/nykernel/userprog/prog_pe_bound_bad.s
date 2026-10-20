/*
 * prog_pe_bound_bad.s -- the BOUND-IMPORT control carrier (ref-aware
 * round): a real native-subsystem PE32+ executable whose ONLY import
 * is the forwarder host's crossfn_fwd (GNU ld synthesizes the .idata
 * from the direct dll link, exactly like the main client). Its entry
 * point is never entered; the whole artifact is control machinery.
 *
 * tools/patch_pe_bound.py composes into it a bound-import directory
 * whose single entry
 *   (a) declares the FORWARDER HOST'S OWN .edata TimeDateStamp (a
 *       fresh descriptor -- the host is registered under that stamp),
 *   (b) carries ONE forwarder REF declaring the PRIMARY PROVIDER's
 *       own stamp (all-fresh: the chasing target did not rebuild), and
 *   (c) fills its single IAT slot with a deliberately WRONG value.
 * The honor path is GATED ON (descriptor fresh + ref fresh), so the
 * loader verifies the slot against a fresh resolution -- which lands
 * on the PRIMARY provider's crossfn (the forwarder chase) -- sees the
 * contradiction, and must refuse with the typed BoundValueCorrupt,
 * carrying BOTH the contradicted bound value and the fresh (chased)
 * value that contradicts it. Never trusted blindly.
 *
 * The sibling artifact prog_pe_bound_refstale.s composes the SAME
 * directory with ONLY the ref's stamp bumped by one: the gate flips
 * OFF, the same wrong slot is DISCARDED, and resolution proceeds.
 * One byte of composed stamp decides refuse-vs-discard -- the pair is
 * the ref-aware ruling's demonstration. See tools/patch_pe_bound.py.
 */
    .text
    .global _start
_start:
    mov __imp_crossfn_fwd(%rip), %rax
    int $0x8D
.hang:
    pause
    jmp .hang
