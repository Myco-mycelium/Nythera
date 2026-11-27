#!/usr/bin/env bash
#
# qemu_boot_test.sh -- builds the kernel with the qemu_test feature
# (which makes kernel_main call qemu_exit::success() after reaching
# BootPhase::Ready instead of idling forever), boots it in QEMU
# headless, and turns the guest's isa-debug-exit write into a real
# process exit code: 0 = boot succeeded through Phase 1, nonzero = it
# didn't. This is what CI should run -- not just "does it link", but
# "does it actually reach a running kernel in an emulated CPU."

set -euo pipefail
cd "$(dirname "$0")/.."

# Record what this run is testing before it tests anything: the commit, the
# repo's dirty count and a digest of the kernel source set, so
# tools/make_progress_zip.py can compare content rather than mtimes when it
# asks whether this transcript still describes the tree (tools/source_state.py).
_state_py="$(command -v python3 || command -v python || true)"
if [[ -n "$_state_py" ]]; then
    "$_state_py" "$(cd ../../.. && pwd)/tools/source_state.py" || true
fi

# The environment this gate assumes, named before it is assumed: a missing
# qemu, a path too long for proot's monitor socket, or a clippy-less host now
# fails HERE with a sentence instead of later with a 127 or a hang. --quiet
# prints only the failures and warnings.
if ! ./tools/gate_env.sh --quiet; then
    echo "FAIL: the gate environment is not ready (the requirements above are missing)"
    exit 1
fi

# gate_env calls a missing grub-mkrescue a blocked gate, not an unready host,
# because the discs can fall back to grub-mkimage + xorriso (see
# make_live_iso.sh). This gate builds its own ISO, so it takes the same two
# routes as the discs instead of stopping at a tool this host may not have --
# grub-mkrescue is a Unix shell wrapper over grub-mkimage, and Windows hosts
# carry the latter without the former. Require the pair the fallback needs,
# named, before the build rather than failing after it.
if ! command -v grub-mkrescue >/dev/null 2>&1; then
    command -v grub-mkimage >/dev/null 2>&1 || {
        echo "FAIL: neither grub-mkrescue nor grub-mkimage is on PATH: no test ISO can be assembled"
        exit 1
    }
    command -v xorriso >/dev/null 2>&1 || {
        echo "FAIL: grub-mkimage is on PATH but xorriso is not: no test ISO can be assembled"
        exit 1
    }
fi

./build.sh --qemu-test

ISO_DIR=target/isofiles-test
rm -rf "$ISO_DIR"
mkdir -p "$ISO_DIR/boot/grub"
cp target/kernel.elf "$ISO_DIR/boot/kernel.elf"
cat > "$ISO_DIR/boot/grub/grub.cfg" <<'EOF'
set timeout=0
set default=0
menuentry "Nyrqis Kernel (test)" {
    multiboot2 /boot/kernel.elf
    boot
}
EOF
if command -v grub-mkrescue >/dev/null 2>&1; then
    grub-mkrescue -o target/nyrqis-test.iso "$ISO_DIR" >/dev/null 2>&1
else
    # The grub-mkrescue-free route, with the same El Torito construction and
    # module list make_live_iso.sh uses: biosdisk + iso9660 are the CD boot
    # path (GRUB 2 has no `cdrom` module), multiboot2 loads this kernel, and
    # `-p '(cd)/boot/grub'` names the device so the core finds the config.
    GRUB_MODDIR="${NYKERNEL_GRUB_MODULES:-$(dirname "$(command -v grub-mkimage)")/i386-pc}"
    [[ -d "$GRUB_MODDIR" ]] || { echo "FAIL: GRUB module directory $GRUB_MODDIR not found" >&2; exit 1; }
    grub-mkimage -O i386-pc-eltorito -d "$GRUB_MODDIR" -p '(cd)/boot/grub' \
        -o "$ISO_DIR/boot/grub/eltorito.img" \
        biosdisk iso9660 normal search search_fs_file search_label \
        multiboot2 all_video font gfxterm gfxterm_background video video_fb png jpeg \
        terminal relocator boot echo test configfile ls cat sleep serial minicmd halt reboot \
        part_msdos part_gpt
    [[ -s "$ISO_DIR/boot/grub/eltorito.img" ]] || { echo "FAIL: grub-mkimage produced no eltorito image" >&2; exit 1; }
    xorriso -as mkisofs -o target/nyrqis-test.iso -V NYRQIS \
        -b boot/grub/eltorito.img -no-emul-boot -boot-load-size 4 -boot-info-table \
        "$ISO_DIR"
fi

set +e
# 120 s settle-bound, catalogued: the boot under a fast desktop host
# takes ~5 s, but the ubuntu-26 CI runners (QEMU 10.2 on shared cores)
# cut the 15 s cap's full demo chain in half mid-run -- the transcript
# reached the LAST demo (kernel boot-gate run 2026-10-24) before the
# kill. A required gate should only fail on the kernel's behavior, not
# on how fast an arbitrary runner image happens to be, so the cap is
# set wall-clock generously on purpose.
#
# -no-shutdown is DROPPED below (catalogued 2026-10-24, live CI
# catch): QEMU 10.x's isa-debug-exit write no longer calls exit()
# directly -- it routes through
# qemu_system_shutdown_request_with_code(), which HONORS -no-shutdown
# by stopping the VM instead of exiting the process, so the guest sat
# stopped after the last demo line until the timeout kill
# ("terminating on signal 15") on the ubuntu-26 runner's QEMU 10.2.1
# (verified against qemu master's hw/misc/debugexit.c: the write
# handler is qemu_system_shutdown_request_with_code, not exit()).
# On 8.x the flag was an inert no-op
# beside the device's direct exit(), and the exit code contract
# ((value << 1) | 1) is unchanged, so the removal is safe on both
# sides. -no-reboot stays: it is the reboot-loop guard.
#
# The cap was 120 s until 2026-11-21, when it was raised to 600 s: the
# boot under pure TCG on a shared cloud VM (no KVM, measured on the
# increment below) needs ~4 minutes to reach the last ring-3 demo, and
# at 120 s the guest was still rendering the platform-mode cycle --
# the transcript for the SAME ISO stopped there with and without that
# increment, so the cap was failing the gate on host speed alone, which
# is exactly what the paragraph above says a required gate must not do.
# 600 s keeps a hang bounded while leaving the slowest runner seen
# ~2.5x headroom.
timeout 600 qemu-system-x86_64 \
    -cdrom target/nyrqis-test.iso \
    -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
    -serial stdio \
    -display none \
    -no-reboot \
    > target/qemu-test-stdout.log 2>&1
QEMU_STATUS=$?
set -e

echo "--- guest serial output ---"
cat target/qemu-test-stdout.log
echo "----------------------------"

# isa-debug-exit maps guest value V to host exit code (V << 1) | 1.
# success() writes 0x10 -> host exit code 33.
if [[ "$QEMU_STATUS" -eq 33 ]]; then
    # Pin the blocking-receive demo's OWN lines as gate output: the exit
    # code alone proves Ready was reached, not that every self-test ran
    # with an OK verdict. These greps fail the gate if the NPS-003 §3
    # park-and-wake proof (tasks C/D/E) deviates from its canonical
    # tape -- broadly checked modulo variable-free substrings, in
    # ORDER (the demo's sequence is part of the contract).
    for needle in \
        "blocking receive leg 1: parked empty, 1 empty wake, budget exhausted -> ReceiveTimedOut -- OK" \
        "kicked C's parked timeout leg once" \
        "woke BOTH parked delivery-leg receivers and sent the race message" \
        "wakes raced against C: consumed the race message" \
        "woke C's re-parked delivery leg and sent the real delivery" \
        "blocking receive leg 2: woken BY A SEND" \
        "experimental ipc blocking-receive proof: task C parked, got 1 empty wake" \
        "service-0x84 gate park self-test: the 0x46 blocking dial PARKED a session-backed task" \
        "service-0x84 call proof self-test: the caller parked (state BLOCKED)" \
        "and the caller resumed with 8 bytes reading 0x6b636162676e6f70" \
        "experimental win32-demo: kernel32.dll loaded at its own ImageBase 0x190000000000" \
        "experimental win32-demo: second provider nyrqis.dll loaded at its own ImageBase 0x190100000000" \
        "experimental win32-demo: resolved 22 import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (CloseHandle, CreateFileA, ExitProcess, FreeLibrary, GetCommandLineA, GetFileSize, GetLastError, GetModuleFileNameA, GetModuleHandleA, GetProcAddress, GetProcessHeap, GetStdHandle, GetTickCount, HeapAlloc, HeapFree, HeapReAlloc, HeapSize, LoadLibraryA, ReadFile, SetFilePointer, SetLastError, WriteFile), every slot reading back its export address -- OK" \
        "experimental win32-demo: negative control 6 -- the read-only volume refused a relative path" \
        "WriteFile returned 1 and wrote 33 byte(s)" \
        "bytes.bin read back byte-identically" \
        "empty.txt read TRUE with 0 byte(s)" \
        "a missing file returned 0xffffffffffffffff" \
        "experimental win32-demo: negative control 7 -- the last-error cell started at ERROR_SUCCESS" \
        "GetLastError answered ERROR_FILE_NOT_FOUND" \
        "GENERIC_WRITE was refused with ERROR_ACCESS_DENIED" \
        "SetLastError(0x1234) read back as 0x1234" \
        "experimental win32-demo: negative control 8 -- a refused call answers with its export's own Windows failure value" \
        "GENERIC_WRITE was refused with ERROR_ACCESS_DENIED (5), returned 0xffffffffffffffff rather than 0 and passed the client's own INVALID_HANDLE_VALUE handle test (1)" \
        "experimental win32-demo: negative control 9 -- GetCommandLineA was refused with the typed LoaderStateMissing before any loader placed a string" \
        "GetModuleHandleA(NULL) answered the client's own image base 0x1a0000000000" \
        "experimental win32-demo: negative control 10 -- with no module registered, GetProcAddress was refused as LoaderStateMissing" \
        "nyrqis.dll's declared OrdinalBase 10 made its first two exports ordinals 10 and 11 (both answered the addresses their NAMES did)" \
        "ordinals 9 and 15 (below that base and past its last slot) answered ProcNotFound rather than an index guess" \
        "experimental win32-demo: forwarded exports -- nyrqis.dll's NyrqisTick (a forwarder to kernel32.dll.GetTickCount) answered 0x19000000104d" \
        "NyrqisGhost (a forwarder to kernel32.dll.NoSuchExport) answered ProcNotFound with ERROR_PROC_NOT_FOUND (127)" \
        "experimental win32-demo: runtime modules -- nyrqis.dll's forwarder NyrqisDyn answered ProcNotFound with no nyrqis_dyn.dll row" \
        "LoadLibraryA(\"C:\\nyrqis\\nyrqis_dyn.dll\") answered the image's own base 0x190200000000 and made it a fourth row, a SECOND load of the same file answered the same base with no new row, a fifth registration was refused as ModuleTableFull (4 rows)" \
        "one FreeLibrary left it loaded with the forwarder still resolving, the second dropped the row so the name answered ERROR_MOD_NOT_FOUND (126) and the forwarder ERROR_PROC_NOT_FOUND (127)" \
        "a DLL the volume does not have answered NULL with ERROR_MOD_NOT_FOUND (126) -- OK, the module table grows and shrinks while a program runs" \
        "experimental win32-demo: module files -- GetModuleFileNameA(NULL) answered \"C:\\nyrqis\\win32_console.exe\" (27 bytes) for this process's own row, kernel32.dll's handle answered \"C:\\nyrqis\\kernel32.dll\" and nyrqis.dll's \"C:\\nyrqis\\nyrqis.dll\"" \
        "the RUNTIME row answered the path LoadLibraryA was given while it was loaded, an 8-byte buffer cut that 27-byte path to 7 bytes plus NUL and answered 8 with ERROR_INSUFFICIENT_BUFFER (122)" \
        "nSize 0 wrote nothing and answered 0 with the same code, and the freed row's base was refused as ModuleNotLoaded" \
        "GetModuleFileNameA asked of that row answered the very path LoadLibraryA was given" \
        "asked WHICH FILE each handle came from, GetModuleFileNameA answered 27 bytes for the client's own row (its own byte-and-NUL compare 1 it), 22 for kernel32.dll and 24 for the runtime row while it was loaded" \
        "kernel32.dll, nyrqis.dll and win32_console.exe then registered as three rows" \
        "all 22 kernel32 exports and both nyrqis exports resolved BY NAME from their OWN export directories (22+2)" \
        "kernel32.dll asked for NyrqisBuild answered ProcNotFound and nyrqis.dll asked for GetTickCount answered ProcNotFound (so the rows are per-module)" \
        "experimental elf-dyn-demo: the same .so linked WITHOUT -z relro loaded at" \
        "GNU_RELRO outcome absent (None), GLOB_DAT slot" \
        "(resolved dyn_value), RELATIVE slot" \
        "(base + addend) -- OK, relocations are not gated on a RELRO region" \
        "the relro-less NEGATIVE CONTROL" \
        "was refused with UndefinedSymbol" \
        "unsatisfiable relocation inside a relro-less object fails the load instead of leaving a zeroed slot" \
        "experimental win32-demo: negative control 3b -- GetProcessHeap answered the surface's handle; a 32-byte HEAP_ZERO_MEMORY block landed 16-byte aligned inside the 8192 byte region and read back zero; HeapFree answered TRUE for it" \
        "experimental win32-demo: the process heap -- GetProcessHeap answered 0x48" \
        "experimental win32-demo: negative control 3c -- a 64-byte block's HeapSize answered 64, HeapReAlloc(128) moved it and kept the byte written through it, the new block's HeapSize answered 128, an in-place-only grow was refused as HeapExhausted with ERROR_NOT_ENOUGH_MEMORY (8) leaving the block at 128, and HeapSize of the moved-from address was refused as HeapBlockNotLive" \
        "experimental win32-demo: the heap's size and reallocation -- HeapSize of the live 32-byte block answered 32" \
        "HeapReAlloc(48) answered a DIFFERENT block" \
        "whose HeapSize answered the REUSED block's own size 128 rather than 48, the stated no-split limit (1)" \
        "a shrink to 16 answered the same pointer (1)" \
        "an in-place-only grow to 4096 answered 0x0 with ERROR_NOT_ENOUGH_MEMORY (8)" \
        "a CRT's malloc/realloc/free triple now runs through this kernel" \
        "experimental dex-demo: class tables -- a 294 byte DEX with 3 string(s), 2 type(s), 1 class(es)" \
        "string 0/1/2 answer \"Lnyrqis/hello/Hello;\"/\"Ljava/lang/Object;\"/\"Hello.java\"" \
        "experimental dex-demo: table refusals -- a string_data offset past EOF refused StringDataOutOfBounds" \
        "a declared UTF-16 length of 19 for 20 units refused StringLengthMismatch" \
        "a class descriptor that does not start with L refused BadTypeDescriptor -- OK" \
        "experimental dex-demo: method tables -- a 436 byte DEX with 1 proto(s) and 1 method(s)" \
        "proto 0's shorty \"VL\" matches return type V and 1 parameter [Ljava/lang/String;" \
        "signature main([Ljava/lang/String;)V (flags 0x109, ACC_PUBLIC|ACC_STATIC|ACC_NATIVE)" \
        "experimental dex-demo: method refusals -- a proto index past the table refused ProtoIndexOutOfBounds" \
        "a class_data whose counts overrun the file refused ClassDataTruncated" \
        "a shorty disagreeing with its parameter count refused ShortyLengthMismatch" \
        "a class_data field index past field_ids refused FieldIndexOutOfBounds, and a class_data method index past method_ids refused MethodIndexOutOfBounds -- OK" \
        "experimental dex-demo: field and code tables -- a 604 byte DEX with 3 field(s), 3 method(s) and 2 code_item(s): field 2 composes to Lnyrqis/hello/Fields;.name:Ljava/lang/String;" \
        "class_data answers static field indices [0, 1], instance [2] (its own delta, not the static list's accumulator) and direct method indices [0, 1, 2]" \
        "size()I carries a 2-word code_item at 0x194 reading [0x0012, 0x000f] (const/4 v0, #0; return v0)" \
        "while make()V is ACC_NATIVE with code_off 0" \
        "experimental dex-demo: member refusals -- a field index past field_ids refused FieldIndexOutOfBounds" \
        "a native method carrying a non-zero code_off refused MethodCodeMismatch before the offset was resolved" \
        "a code_item whose words run past EOF refused CodeItemTruncated" \
        "one whose ins_size exceeds its registers refused CodeItemRegisterMismatch" \
        "a class_data replacement one byte short, one byte long or an encoding grown past the span refused by the span rule" \
        "and code_item(0) refused CodeItemOutOfBounds -- OK" \
        "experimental dex-demo: annotation tables -- a 744 byte DEX whose class Lnyrqis/hello/Annotated; names an annotations directory with 1 class, 1 field, 1 method and 1 parameter row(s): its class annotation is VISIBILITY_RUNTIME on Lnyrqis/hello/Marker;" \
        "with elements count=7 (an int), name=\"hello\" (a string) and flags=[true, false, [\"hello\"]] (nested arrays two deep), its field/method/parameter rows resolve to a BUILD item, a SYSTEM item and a RUNTIME item on member 0," \
        "and its static_values encoded array reads SEED=1000 -- the two-byte VALUE_INT whose width field a reader that ignored it would have read as 232 -- then label=\"hello\"; classes_fields.dex, whose class names no directory and no static values, answers None and an empty array -- OK" \
        "experimental dex-demo: annotation refusals -- a class_def annotations_off past EOF refused AnnotationsDirectoryOutOfBounds, a directory whose field count overruns the file refused AnnotationsDirectoryTruncated, a class_annotations_off past EOF refused AnnotationSetOutOfBounds, a set asked at offset zero refused AnnotationSetOutOfBounds, a set whose size overruns the file refused AnnotationSetTruncated, an annotation item past EOF refused AnnotationItemOutOfBounds, a visibility byte of 3 refused BadAnnotationVisibility," \
        "a value type 0x07 refused BadEncodedValueType, an int value claiming seven bytes refused BadEncodedValueArg, an annotation type index past type_ids refused TypeIndexOutOfBounds, a string value naming string 127 refused StringIndexOutOfBounds, a directory row naming field 5 refused FieldIndexOutOfBounds, a static_values_off past EOF refused EncodedArrayOutOfBounds, an array whose count overruns the file refused EncodedArrayTruncated, and arrays nested nine deep refused EncodedValueTooDeep -- OK" \
        "experimental dex-demo: map sections -- a 748 byte DEX whose map lists 20 entries in ascending offset order" \
        "call_site_ids at 0xf4, method_handles at 0xf8" \
        "debug_info at 0x1b0, encoded_array, annotation_set_ref_list at 0x1c8" \
        "handle 0 is a STATIC_GET of field 0 and handle 1 an INVOKE_STATIC of method 0, call site 0 reads the three-element array [handle 1, \"handler\", proto 0]" \
        "the parameter row resolves through one ref-list entry to a RUNTIME Lnyrqis/hello/Tag; with note=5" \
        "and the debug_info item states line 1, one parameter \"count\", positions (0,1) and (1,2), and one local -- the class fixture's six-entry map reads back too -- OK" \
        "experimental dex-demo: map refusals -- a map item type 0x0009 refused UnknownMapItemType, an entry stepping back to offset 0 refused MapNotSorted, a method_handles section claiming 8192 items past EOF refused MapSectionOutOfBounds, a string_ids count of 12 against the header's 13 refused MapSectionMismatch, a header_item of two items refused MapSectionMismatch, a map_list entry of size 2 refused MapSectionMismatch, a map whose entry is retagged so it no longer describes itself refused NoMapListEntry" \
        "a file with NO method_handles asked for one refused MethodHandleSectionMissing, handle index 2 past a two-handle section refused MethodHandleIndexOutOfBounds, handle type 0x09 refused BadMethodHandleType" \
        "a file with NO call_site_ids asked for one refused CallSiteSectionMissing, call-site index 1 refused CallSiteIndexOutOfBounds, a call site whose array holds two values refused BadCallSiteShape" \
        "a ref list past EOF refused AnnotationSetRefListOutOfBounds, one asked at zero refused AnnotationSetRefListOutOfBounds, one whose size overruns the file refused AnnotationSetRefListTruncated, one naming a set past EOF refused AnnotationSetOutOfBounds, a directory parameter row naming zero instead of a list refused AnnotationSetRefListOutOfBounds" \
        "a debug_info past EOF refused DebugInfoOutOfBounds, one asked at zero refused DebugInfoOutOfBounds, an undefined debug opcode 0x05 refused BadDebugInfoOpcode, a debug item cut before its parameter name refused DebugInfoTruncated, and one naming string 126 refused StringIndexOutOfBounds -- OK" \
        "experimental dex-demo self-test: crypto known-answer OK, valid parse+integrity OK, bad-magic rejection OK, map-self-reference rejection OK, checksum rejection OK, class tables OK, table refusals OK, method tables OK, method refusals OK, field and code tables OK, member refusals OK, annotation tables OK, annotation refusals OK, map sections OK, map refusals OK -- OK" \
        "the next 16-byte allocation answered" \
        "-- OK, a real Windows program can allocate and free its own memory through this kernel" \
        "experimental win32-demo self-test checks: console=true hello=true bytes=true empty=true closed=true miss=true errors=true monotonic=true exit=true handles=true refusals=true handle_test=1 startup=true bindings=true modules=true ordinals=true forwarders=true dynamic=true files=true heap=true realloc=true cursor=true" \
        "experimental win32-demo: a file's size and cursor -- GetFileSize(hello.txt) answered 33 byte(s) with the high dword written as 0" \
        "SetFilePointer(3, FILE_BEGIN) answered 3 and the read there answered 4 byte(s) reading \"lo f\"" \
        "a zero move from FILE_CURRENT answered 7" \
        "a MOVE OF -4 FROM FILE_END answered 29 and the read there answered" \
        "a seek to -100 answered 0xffffffff with ERROR_NEGATIVE_SEEK (131) leaving the cursor at 33" \
        "an unknown move method answered 0xffffffff with ERROR_INVALID_PARAMETER (87)" \
        "GetFileSize of the console handle answered 0xffffffff with ERROR_INVALID_HANDLE (6)" \
        "GetCommandLineA answered 0x4d00000002a0 on both calls with the client's own byte-and-NUL compare 1 it" \
        "GetModuleHandleA(NULL) answered the client's image base 0x1a0000000000 with the client's _start-inside-image test 1 it" \
        "GetModuleHandleA(\"kernel32.dll\") answered the provider's own base 0x190000000000 in either case" \
        "GetProcAddress named GetTickCount at 0x19000000104d" \
        "a second provider, nyrqis.dll, answered its own row's base 0x190100000000 (different from kernel32's)" \
        "resolved there at 0x190100001000 and 0x190100001006, which the client CALLED through, reading 6 and 1" \
        "kernel32.dll asked for NyrqisBuild answered 0x0 with ERROR_PROC_NOT_FOUND (127) and nyrqis.dll asked for GetTickCount answered 0x0 the same (127)" \
        "asked in Windows' ordinal form, the second provider's base-11 ordinal 11 answered 0x190100001006 -- the same address its NAME did (1) -- which the client CALLED through, reading 1" \
        "ordinals 9 and 15 (below the base, past the last slot) answered 0x0 and 0x0 with ERROR_PROC_NOT_FOUND (127) and (127)" \
        "its forwarded export NyrqisTick (kernel32.dll.GetTickCount) answered 0x19000000104d BY NAME and BY ORDINAL 12" \
        "a forwarder to an export kernel32.dll does not have answered 0x0 with ERROR_PROC_NOT_FOUND (127)" \
        "BEFORE LoadLibraryA, nyrqis.dll's forwarder NyrqisDyn (naming nyrqis_dyn.dll.NyrqisDynValue) answered 0x0 with ERROR_PROC_NOT_FOUND (127), LoadLibraryA(\"C:\\nyrqis\\nyrqis_dyn.dll\") then answered the runtime image's own base 0x190200000000" \
        "the SAME forwarder then answered 0x190200001000 -- the dynamic row's own export at +0x1000 -- which the client CALLED through, reading 42, FreeLibrary answered 1 and the forwarder went back to 0x0 with ERROR_PROC_NOT_FOUND (127), and a DLL the volume does not have answered 0x0 with ERROR_MOD_NOT_FOUND (126)" \
        "a module that is not loaded answered 0x0 with ERROR_MOD_NOT_FOUND (126) and a name that module does not export answered 0x0 with ERROR_PROC_NOT_FOUND (127)" \
        "-- OK, a real Windows-format console program started the way a CRT does" \
    ; do
        if ! grep -aqF -- "$needle" target/qemu-test-stdout.log; then
            echo "FAIL: blocking-receive demo line missing or out of contract: $needle"
            grep -a "task C\|task D\|task E\|blocking-receive" target/qemu-test-stdout.log | head -10
            exit 1
        fi
    done
    echo "PASS: kernel reached BootPhase::Ready and exited cleanly; the C/D/E blocking-receive tape, the gate-park proof and the cross-session blocking call are pinned and matched"
    exit 0
elif [[ "$QEMU_STATUS" -eq 124 ]]; then
    echo "FAIL: boot test timed out (kernel hung or never reached Ready)"
    exit 1
else
    echo "FAIL: unexpected QEMU exit status $QEMU_STATUS"
    exit 1
fi
