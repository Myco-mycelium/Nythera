/*
 * prog_pe_bound_ref2bad.s -- the forwarder-REF increment's
 * multi-hop FIRST carrier (TWO refs, both fresh): a real
 * native-subsystem PE32+ executable whose ONLY import is the forwarder
 * host's crossfn_fwd, byte-for-byte the sibling of
 * prog_pe_bound_bad.s. Its entry point is never entered.
 *
 * tools/patch_pe_bound.py composes into it a bound-import directory
 * whose single entry declares:
 *   (a) the FORWARDER HOST'S OWN .edata TimeDateStamp (a fresh
 *       descriptor, exactly like the bad client),
 *   (b) TWO forwarder refs, both declaring their OWN modules' real
 *       stamps -- the PRIMARY PROVIDER's, then the FORWARDER HOST's
 *       own: two DIFFERENT registered modules, both fresh. If the
 *       walk consumed refs by a one-ref assumption instead of the
 *       composed NumberOfModuleForwarderRefs count, this composition
 *       would not parse at the described stamps at all,
 *   (c) fills its single IAT slot with the SAME deliberately WRONG
 *       value every probe carrier carries.
 * All-fresh everywhere means the honor gate is GATED ON purely by
 * stamps -- so the per-slot VALUE verification (which runs the
 * forwarder chase) must refuse with the typed `BoundValueCorrupt`,
 * exactly as the one-ref bad client does. The sibling
 * prog_pe_bound_ref2stale.s moves only the SECOND ref's stamp to
 * prove that moving a LATER ref, not the first, still flips the gate.
 * See tools/patch_pe_bound.py.
 */
    .text
    .global _start
_start:
    mov __imp_crossfn_fwd(%rip), %rax
    int $0x8D
.hang:
    pause
    jmp .hang
