/*
 * prog_pe_import_fwdprov.s -- the FORWARDER-HOST provider DLL of the
 * PE32+ import-resolution increment: a second real Windows-format
 * provider whose three exports START as ordinary stubs the ordinary
 * linker emits, and whose EAT entries the build-time patcher then
 * composes into forwarded-export STRING references. The composition
 * is disclosed exactly like the weak-VERNEED patch: the toolchain
 * here does not emit forwarders naturally, so the checked patcher
 * builds the format-legal structures by hand and self-verifies them.
 *
 * The three exports:
 *
 *   crossfn_fwd     -- the demo's POSITIVE chain: the client imports
 *                      it named from this DLL; the kernel's registry
 *                      chase follows the composed string
 *                      "userprog_pe_import_dll.dll.crossfn" and the
 *                      slot in the end holds the provider A crossfn's
 *                      address -- the client executes PROVIDER A's
 *                      own code through this DLL's export entry.
 *   crossfn_cycle   -- NEGATIVE control only (registry-level probe,
 *                      never imported): its EAT entry becomes a
 *                      forwarder naming ITSELF
 *                      "userprog_pe_import_fwdprov.dll.crossfn_cycle",
 *                      a chain that can only ever re-enter the same
 *                      table forever -> the typed
 *                      `ForwarderDepthExceeded` refusal.
 *   crossfn_nodll   -- NEGATIVE control only (registry-level probe):
 *                      its EAT entry becomes
 *                      "nobody.dll.crossfn" -> the registry has NO
 *                      entry for that name -> the typed
 *                      `MissingDllImport` refusal. The name-vs-file
 *                      ruling applies to forwarders exactly as it
 *                      applies to import descriptors.
 */
    .text
    .global crossfn_fwd
crossfn_fwd:
    mov $0xfa571ae5, %eax
    ret
    .global crossfn_cycle
crossfn_cycle:
    mov $0xc0ffee, %eax
    ret
    .global crossfn_nodll
crossfn_nodll:
    mov $0xdead, %eax
    ret
