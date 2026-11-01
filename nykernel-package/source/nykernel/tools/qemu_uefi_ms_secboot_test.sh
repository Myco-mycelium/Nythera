#!/usr/bin/env bash
#
# qemu_uefi_ms_secboot_test.sh -- the secure-boot-enforcing twin of
# qemu_uefi_boot_test.sh. Same kernel+stub build, but the firmware is
# the enforcing OVMF (-4M.ms) CODE image and the variable store is
# FRESHLY composed from the PLAIN variable-store template with the
# carrier chain itself: the ADR-0014 root pair as PK/KEK, root+leaf
# certificates in db, SecureBootEnable on. The LEAF cert additionally
# goes into MokList under shim's SHIM_LOCK_GUID (ADR-0015's content
# decision: a user enrollment carries the LEAF signer, never the root
# anchor -- enrolling the root would grant every future leaf it ever
# issues), so the positive leg also proves the stub reads a REAL shim
# MokList variable on real enforcing firmware and names its facts. The plain (not .ms)
# template is deliberate: virt-fw-vars can only ADD entries to a
# template it reads, never remove them, so a .ms-template copy would
# keep the template's Microsoft authorities in KEK/db -- the written
# store would not be the store this gate claims to prove against.
# The stub is then Authenticode-signed with
# the leaf key (sbsign) and installed on the ESP, so the FIRMWARE's
# verification -- not ours -- is the gate the stub passes, and the
# stub's own carrier walk (which anchors the same root) still enforces
# the kernel image inside it. FIVE boots assert all directions:
#
# positive: signed stub under enforcement -> firmware accepts it, the
#   stub reads SecureBoot=1 with PK present (not SetupMode), the
#   carrier chain verifies over the kernel, the stub ALSO walks the
#   real MokList variable and names its leaf entry covering the
#   just-verified chain (the MOK path extending the embedded anchor,
#   both facts pinned), and the boot reaches Ready
#   (host exit code 33, the same contract as the non-ms gate).
# negative (unsigned): the UNSIGNED stub against the same enforcing
#   store -> the firmware refuses it at the boot manager ("Access
#   Denied") and the boot never reaches the stub (host timeout
#   catches the idle loop).
# negative (tampered): the SAME signed image with ONE flipped byte
#   inside a hash-covered section -> the signature stops matching the
#   bytes it covers and the firmware must refuse it the same way;
#   enforcement covers signed-but-modified images, not only images
#   with no signature at all.
# negative (revoked): a SECOND enforcing store, identical enrollment
#   PLUS the LEAF signer's To-Be-Signed hash as an EFI_CERT_X509_SHA256
#   dbx entry (revoked 2020, so any boot clock after that date hits it)
#   -- the exact real-world hash-typed revocation form Microsoft dbx
#   updates use, delivered to the store through the authenticated-update
#   header virt-fw's --set-dbx parses. The leaf-signed stub (and the
#   MokList-enrolled signer it rides on) must be REFUSED: either by the
#   firmware itself (Access Denied, the boot manager's loop), or -- if
#   this OVMF build's dbx hash check is absent -- by the STUB's own
#   revocation walk over the REAL firmware-served dbx variable. Either# refusal is a PASS because both prove the same fact: the revocation
#   leg is wired through real firmware bytes and enforced, and every
#   refusal names its stage. WHICH party refused is now asserted as a
#   pinned fact in the verdict (it used to be a printed note): the leg
#   also requires ADR-0029's unconditional `dbx: firmware served <n>
#   signature-list bytes; type GUID (...)` line, with <n> equal to the
#   SigDB this gate composed and the GUID bytes equal to
#   EFI_CERT_X509_SHA256's canonical serialization -- so the leg can no
#   longer pass on a refusal that never saw our bytes.
#
#   The FITH boot is the stub's OTHER dbx parser branch given the same
#   treatment: the leaf certificate in dbx as EFI_CERT_X509 (the
#   whole-certificate form, ADR-0014's byte-match rule), delivered the
#   same way. Both branches now have a firmware-served enforcing leg,
#   not just the host harness and the stub's embedded negatives, and
#   both legs assert the same two facts (served shape, refusing party).
#
# The POSITIVE boot also covers ADR-0029's UPDATE side, which the host
# gate (tools/dbx_update_gate.sh) cannot reach: that store carries a
# 1-byte trigger in the stub's own vendor namespace, and the stub then
# writes its embedded KeK-signed EFI_VARIABLE_AUTHENTICATION_2 package
# to dbx through SetVariable -- so the FIRMWARE verifies the bare
# PKCS#7 SignedData over (VariableName, VendorGuid, Attributes, EFI_TIME,
# payload) and stores it. Asserted: the non-KeK-signed twin is REFUSED
# by the firmware first (its stamp cannot explain the refusal), the
# KeK-signed package is ACCEPTED, the read-back carries the exact entry
# it signed, and the trigger is consumed so a re-boot does not replay a
# now-stale timestamp. No other store carries the trigger, so the
# revocation/probe stores' composed dbx entries are never replaced.
#
# This is what CI should run to prove the ms OVMF path, the same way
# qemu_uefi_boot_test.sh proves the default OVMF path.

set -euo pipefail
cd "$(dirname "$0")/.."

# OVMF_CODE_OVERRIDE runs the SAME assertions against a different
# firmware build -- the cross-build measurement ADR-0029's Implementation
# Note asks for. The image's sha256 goes into the transcript, so a verdict
# always names the firmware it describes instead of implying "OVMF".
FAKECODE="${OVMF_CODE_OVERRIDE:-uefi/tools/OVMF_CODE_4M.ms.fd}"
VARS_TMPL="${OVMF_VARS_OVERRIDE:-uefi/tools/OVMF_VARS_4M.fd}"
for f in "$FAKECODE" "$VARS_TMPL"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: OVMF image missing: $f"
        exit 1
    fi
done
FIRMWARE_SHA="$(sha256sum "$FAKECODE" | cut -d' ' -f1)"
echo "==> firmware under test: $FAKECODE (sha256 $FIRMWARE_SHA)"

# Same build order and stub path as qemu_uefi_boot_test.sh: kernel
# BEFORE stub, because the stub embeds the kernel image bytes.
./build.sh --qemu-test
if ! command -v rust-lld >/dev/null 2>&1; then
    _sysroot="$(rustc --print sysroot 2>/dev/null || true)"
    if [[ -n "$_sysroot" ]]; then
        _rust_lld="$(find "$_sysroot" -name rust-lld -type f 2>/dev/null | head -n1)"
        if [[ -n "$_rust_lld" ]]; then
            export PATH="$(dirname "$_rust_lld"):$PATH"
        fi
    fi
fi
(cd uefi && RUSTC_BOOTSTRAP=1 cargo build --target x86_64-unknown-uefi)
STUB_EFI=uefi/target/x86_64-unknown-uefi/debug/nyrqis_uefi_stub.efi
if [[ ! -f "$STUB_EFI" ]]; then
    echo "FAIL: stub built but not found at $STUB_EFI"
    exit 1
fi

for tool in sbsign sbverify mformat mmd mcopy qemu-system-x86_64 python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "FAIL: required tool missing: $tool"
        exit 1
    }
done
VIRT="$(command -v virt-fw-vars 2>/dev/null || echo "$HOME/.local/bin/virt-fw-vars")"
if [[ ! -x "$VIRT" ]]; then
    echo "FAIL: virt-fw-vars missing (pip name: virt-firmware) -- enrollment cannot be composed"
    exit 1
fi

# --- compose the enforcing store from the PLAIN template ---
# See the header: the .ms template's own keys would MERGE into the
# composition (virt-fw-vars adds, never removes), so the store starts
# from the plain template and receives exactly the carrier chain.
ROOT_CERT=uefi/tools/trust/carrier-dev-cert.pem
LEAF_CERT=uefi/tools/trust/carrier-dev-leaf-cert.pem
LEAF_KEY=uefi/tools/trust/carrier-dev-leaf-key.pem
for f in "$ROOT_CERT" "$LEAF_CERT" "$LEAF_KEY"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: carrier trust material missing (run the build first): $f"
        exit 1
    fi
done
GLV=8be4df61-93ca-11d2-aa0d-00e098032b8c   # EFI_GLOBAL_VARIABLE
ISD=d719b2cb-3d3a-4596-a3bc-dad00e67656f   # EFI_IMAGE_SECURITY_DATABASE_GUID
# shim's SHIM_LOCK_GUID, the vendor GUID shim reads every MokListRT/
# MokNew/MokAuth state variable from -- verified line-for-line against
# rhboot/shim lib/guid.c, the same value virt-fw-vars' Shim GUID and
# the stub's efi.rs pin.
SHIM=605dab50-e046-4300-abb6-3dd810dd8b23
STORE=uefi/dist/VARS.ms-secboot.fd
cp "$VARS_TMPL" "$STORE"
# NOTE: --inplace (or -o) is REQUIRED to write back; virt-fw-vars with
# -i alone only PLANS the changes -- the store on disk keeps the
# template as-is, and the firmware then refuses even a correctly-signed
# stub.
"$VIRT" --inplace "$STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --add-mok "$SHIM" "$LEAF_CERT" \
    --sb > /dev/null
# The authenticated-update trigger (ADR-0029's UPDATE side): a
# variable in the stub's OWN vendor namespace (efi.rs
# VARCHK_VENDOR_GUID), 1 byte 0x01, present ONLY in this store. With it
# the stub performs the KeK-signed dbx write and consumes it; every
# other store in this gate carries no trigger, so its own composed dbx
# entry (revocation legs, probe) is never replaced. Composed through
# virt-fw-vars' --set-json (name, GUID, attributes NV|BS|RT, hex data).
cat > uefi/dist/dbx-update-trigger.json <<'JSON'
{"variables": [{"name": "NyrqisDbxUpdate",
                "guid": "6e1d14a1-2b4f-4b8a-9cd1-374ad1940f2b",
                "attr": 7,
                "data": "01"}]}
JSON
"$VIRT" --inplace "$STORE" --set-json uefi/dist/dbx-update-trigger.json > /dev/null
# Snapshot the trigger store BEFORE the positive boot consumes the
# trigger: the cross-build control below re-uses this exact store and
# package against a different firmware, so it must still ask for the
# write.
CROSS_STORE=uefi/dist/VARS.ms-secboot-crossbuild.fd
cp "$STORE" "$CROSS_STORE"
echo "==> enrolled enforcing store: PK/KEK anchored on the carrier root, root+leaf in db, SecureBootEnable ON, leaf in MokList, dbx-update trigger present"
echo "==> composed store shape (what the firmware will actually read):" && \
    "$VIRT" -i "$STORE" -p 2>/dev/null | grep -E "PK |KEK |db |MokList|SecureBoot" || true

# --- Authenticode-sign the stub with the leaf key ---
# The signer certificate must be one the firmware's db can bind
# directly: the leaf is enrolled verbatim, so AuthenticodeVerify
# matches the signer on sight.
SIGNED_STUB=uefi/dist/nyrqis_uefi_stub-signed.efi
sbsign --key "$LEAF_KEY" --cert "$LEAF_CERT" "$STUB_EFI" --output "$SIGNED_STUB" > /dev/null
sbverify --list "$SIGNED_STUB" | head -4

# --- ONE-byte tamper of the SAME signed image ---
# Flips one bit INSIDE the first hash-covered section (mid-way through
# its raw data), leaving every header byte and the certificate table
# untouched: the PE still parses, the signature is still present, and
# the signed content no longer matches the bytes it covers -- exactly
# the damage enforcement must catch on a signed-but-modified image.
TAMPERED_STUB=uefi/dist/nyrqis_uefi_stub-tampered.efi
python3 - "$SIGNED_STUB" "$TAMPERED_STUB" <<'PYEOF'
import struct, sys
src, dst = sys.argv[1], sys.argv[2]
data = bytearray(open(src, 'rb').read())
pe = struct.unpack_from('<I', data, 0x3c)[0]
assert data[pe:pe + 4] == b'PE\x00\x00', 'tamper probe: not a PE image'
coff = pe + 4
nsec = struct.unpack_from('<H', data, coff + 2)[0]
optsz = struct.unpack_from('<H', data, coff + 16)[0]
table = coff + 20 + optsz
chosen = None
for i in range(nsec):
    s = table + 40 * i
    name, _vsize, _va, rawsize, ptr = struct.unpack_from('<8sIIII', data, s)
    if rawsize > 0:
        chosen = (name.rstrip(b'\x00').decode('latin-1'), ptr, rawsize)
        break
assert chosen is not None, 'tamper probe: no raw section found'
name, ptr, rawsize = chosen
off = ptr + rawsize // 2
assert off + 16 < len(data), 'tamper probe: mid-section reaches the cert area'
before = data[off]
data[off] ^= 0x80
open(dst, 'wb').write(data)
print(f'tamper: flipped top bit at {name}[{off}] '
      f'(0x{before:02x} -> 0x{data[off]:02x}), size unchanged ({len(data)})')
PYEOF
cmp -s "$SIGNED_STUB" "$TAMPERED_STUB" && {
    echo "FAIL: tampered stub is identical to the signed one"
    exit 1
}

# --- three ESP images: signed, unsigned, and tampered-signed ---
mk_esp() {
    rm -f "$1"
    mkdir -p uefi/dist/EFI/BOOT
    mformat -i "$1" -C -T 131072
    mmd -i "$1" ::EFI ::EFI/BOOT
    mcopy -i "$1" "$2" ::EFI/BOOT/BOOTX64.EFI
}
ESP_SIGNED=uefi/dist/esp-signed.img
ESP_UNSIGNED=uefi/dist/esp-unsigned.img
ESP_TAMPERED=uefi/dist/esp-tampered.img
mk_esp "$ESP_SIGNED" "$SIGNED_STUB"
mk_esp "$ESP_UNSIGNED" "$STUB_EFI"
mk_esp "$ESP_TAMPERED" "$TAMPERED_STUB"

boot_qemu() { # $1 esp image, $2 serial log, $3 timeout seconds, $4 store (default: the enforcing store), $5 firmware (default: the gate's)
    local STORE_ELSE="${4:-$STORE}"
    local CODE_ELSE="${5:-$FAKECODE}"
    timeout "$3" qemu-system-x86_64 \
        -machine q35 \
        -m 256 \
        -drive if=pflash,format=raw,readonly=on,file="$CODE_ELSE" \
        -drive if=pflash,format=raw,file="$STORE_ELSE" \
        -drive format=raw,file="$1" \
        -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
        -serial stdio \
        -debugcon file:target/qemu-uefi-ms-ovmf.log \
        -global isa-debugcon.iobase=0x402 \
        -display none \
        -no-reboot \
        > "$2" 2>&1
}

set +e

# --- positive: the signed stub crosses the firmware's own gate ---
echo "==> positive boot: signed stub under enforcement"
boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-positive.log 480
POS_STATUS=$?
echo "--- positive serial (key lines) ---"
# `dbx-update` is in this pattern because the POS_OK check below asserts
# those exact lines: without them in the transcript, a leg that fails the
# check gives a reader no evidence of WHICH line was missing, and an
# external consumer (tools/firmware_matrix.sh classifies a build without
# the auth surface by the SetVariable status printed here) cannot see the
# fact the gate judged on. Measured 2026-11-01: the matrix's first run
# reported two builds as "unclassifiable" purely because this pattern
# omitted the lines, not because their behaviour was unknown.
grep -a -E "SecureBoot|PK variable|carrier verify|Ready|Access Denied|dbx-update" \
    target/qemu-uefi-ms-secboot-positive.log || true
echo "-----------------------------------"
POS_OK=0
if [[ "$POS_STATUS" -eq 33 ]]; then
    if grep -aq "SecureBoot=1" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "PK variable PRESENT" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "carrier verify OK" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "MokList: walked" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "ALSO covers the just-verified chain" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "trigger present -- this store asks for the authenticated write" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "non-KeK-signed package REFUSED by the firmware" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "dbx-update: SetVariable(dbx) ACCEPTED" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "dbx-update: read-back VERIFIED" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "trigger consumed (deleted)" target/qemu-uefi-ms-secboot-positive.log \
       && grep -aq "boot phase = 7 (Ready)" target/qemu-uefi-ms-secboot-positive.log; then
        POS_OK=1
    else
        echo "note: boot reached Ready but a required secboot marker line is missing (see log)"
    fi
elif [[ "$POS_STATUS" -eq 124 ]]; then
    echo "FAIL: positive boot timed out (firmware refused or hung)"
fi

# --- negatives: refusals must belong to the FIRMWARE ---
# 240 s settle per case: the Access-Denied refusal happens in the
# first ~30 s of BdsDxe; anything still alive at 240 s is the refused
# boot sitting in the boot-manager loop, exactly the negative verdict
# we assert: timeout 124, the boot manager's "Access Denied", and NO
# stub line of any kind -- nothing got past the firmware's own check.
refused_by_firmware() { # $1 esp, $2 log, $3 label; sets FW_REFUSED
    FW_REFUSED=0
    boot_qemu "$1" "$2" 240
    S=$?
    echo "--- $3 serial (key lines) ---"
    grep -a -E "Access Denied|Security Violation|Image is not|stub alive" "$2" || true
    echo "-------------------------------"
    if [[ "$S" -eq 124 ]] \
       && grep -aq "Access Denied" "$2" \
       && ! grep -aq "stub alive" "$2"; then
        FW_REFUSED=1
    elif [[ "$S" -eq 33 ]]; then
        echo "FAIL: $3 BOOTED under enforcement -- enforcement is not real"
    else
        echo "note: $3 did not match the expected refusal shape (status $S, see $2)"
    fi
}

echo "==> negative boot 1/4: the same store, the UNSIGNED stub"
refused_by_firmware "$ESP_UNSIGNED" target/qemu-uefi-ms-secboot-negative.log "the unsigned stub"
NEG_OK=$FW_REFUSED

echo "==> negative boot 2/4: the same store, the SIGNED-BUT-TAMPERED stub"
refused_by_firmware "$ESP_TAMPERED" target/qemu-uefi-ms-secboot-tampered.log "the tampered stub"
TAMPER_OK=$FW_REFUSED

# --- shared verdict logic for the revocation legs (there are TWO: one
#     per dbx parser branch the stub implements). Each leg composes its
#     own enforcing store, boots the leaf-signed stub against it, and
#     hands the log to this checker, which pins three facts:
#       - the served shape (ADR-0029): the byte count the stub read
#         equals the SigDB the leg composed, and the type GUID it
#         printed is the branch's canonical serialization -- the GUID is
#         normalized (the printer's prefix/width is not the fact, the
#         byte sequence is) and compared against spec bytes written
#         here, never a repo constant (the ADR-0028 drift lesson);
#       - WHICH party refused (firmware vs the stub's own walk), with
#         the expected, branch-specific refusal text present when the
#         stub is the refusing party -- so a boot that reached the stub
#         without naming the revocation is neither path;
#       - that the stub did NOT simply boot (status 33 is a hard FAIL).
# Sets LEG_OK / LEG_PATH / LEG_SHAPE / LEG_EXPECT_BYTES +
# LEG_EXPECT_GUID_HEX for the caller's final verdict.
dbx_leg_check() { # $1 label, $2 log, $3 auth-update blob, $4 expected refusal text, $5 expected GUID hex, $6 qemu status
    local label="$1" log="$2" update="$3" expect="$4" exp_guid="$5" status="$6"
    LEG_PATH=none
    LEG_SHAPE=none
    LEG_EXPECT_BYTES=$(( $(wc -c < "$update") - 40 ))   # strip the auth header
    LEG_EXPECT_GUID_HEX="$exp_guid"
    local served_line guid_raw guid_hex="" b
    served_line="$(grep -a "dbx: firmware served" "$log" || true)"
    guid_raw="$(printf '%s' "$served_line" | sed -n 's/.*type GUID (\(.*\)).*/\1/p')"
    for b in $guid_raw; do
        guid_hex="${guid_hex}$(printf '%02x' "$b")"
    done
    if [[ "$status" -eq 124 ]] \
       && grep -aq "Access Denied" "$log" \
       && ! grep -aq "nyrqis ubld" "$log"; then
        LEG_PATH=firmware
        echo "-- [$label] stub refused BY THE FIRMWARE (Access Denied before the stub ever ran)"
    elif [[ "$status" -ne 33 ]] \
       && grep -aq "nyrqis ubld: stub alive" "$log" \
       && grep -aq "$expect" "$log"; then
        LEG_PATH=stub
        echo "-- [$label] stub refused BY THE STUB'S OWN WALK over the real firmware-served dbx"
    elif [[ "$status" -eq 33 ]]; then
        echo "FAIL: [$label] the leaf-signed stub BOOTED on this revocation store -- the dbx entry did not bind"
    else
        echo "note: [$label] boot matched NEITHER refusal shape (status $status, see $log) -- counting it as a failure, not as an unnamed third verdict"
    fi
    # --- the served shape, as far as THIS path permits ---
    # Ordering matters and was wrong on this leg's first run: the
    # served-bytes line is printed by the STUB, so on the firmware path
    # there is none BY CONSTRUCTION. Requiring it unconditionally made a
    # firmware-refusing store look like a failure even though the
    # firmware refusing IS path 1's claim (ADR-0029: path 1 proves the
    # composed store is shape-correct for the firmware's own reader).
    # So: the stub path must show the served bytes matching the composed
    # SigDB; the firmware path must show NO stub output at all (already
    # required above) and its shape evidence is the composition-time
    # virt-fw read-back printed with the store -- never an absent line
    # counted as a pass.
    if [[ "$LEG_PATH" == stub ]]; then
        if [[ -z "$served_line" ]]; then
            echo "FAIL: [$label] the stub ran but never printed the served dbx shape -- ADR-0029's unconditional transcript line is missing, so nothing proves the walk saw the composed bytes"
        else
            echo "-- [$label] served dbx shape: ${served_line#  }"
            echo "-- [$label] composed SigDB:   $LEG_EXPECT_BYTES bytes, type GUID ${LEG_EXPECT_GUID_HEX}"
            if [[ "$served_line" == *"firmware served $LEG_EXPECT_BYTES signature-list bytes"* ]] \
               && [[ "$guid_hex" == "$LEG_EXPECT_GUID_HEX" ]]; then
                LEG_SHAPE=verified
            elif [[ "$guid_hex" != "$LEG_EXPECT_GUID_HEX" ]]; then
                LEG_SHAPE=MISMATCH
                echo "FAIL: [$label] the served dbx entry's type GUID is ${guid_hex:-<unparsed>}, not ${LEG_EXPECT_GUID_HEX} -- a type GUID classification drift, or the stub printed a GUID whose bytes are not the spec's"
            else
                LEG_SHAPE=MISMATCH
                echo "FAIL: [$label] the served dbx is not the $LEG_EXPECT_BYTES-byte signature list this gate composed -- served-vs-composed mismatch, so the walk did not act on our bytes"
            fi
        fi
    elif [[ "$LEG_PATH" == firmware ]]; then
        LEG_SHAPE=firmware-refused
        if [[ -n "$served_line" ]]; then
            LEG_SHAPE=MISMATCH
            echo "FAIL: [$label] the FIRMWARE refused yet the stub printed a served-dbx line -- those two facts contradict each other"
        else
            echo "-- [$label] served shape: n/a by construction (the stub never ran); the composed $LEG_EXPECT_BYTES-byte SigDB (type GUID ${LEG_EXPECT_GUID_HEX}) is what the store read-back above shows, and the firmware's own refusal is this path's evidence"
        fi
    fi
    LEG_OK=0
    if [[ "$LEG_PATH" != none ]] \
       && { [[ "$LEG_SHAPE" == verified ]] || [[ "$LEG_SHAPE" == firmware-refused ]]; }; then
        LEG_OK=1
    fi
    echo "-- [$label] leg: refused by $LEG_PATH; served shape $LEG_SHAPE"
}

# --- negative 3: the ENROLLED LEAF is revoked BY HASH in dbx ---
# The hash-typed dbx blob (the leaf's TBS, revoked 2020) already lives
# in uefi/src/dbx-neg-signer-hash.der from make_carrier.sh; wrap it in
# the authenticated-update header virt-fw's --set-dbx parses (the
# wrapping is self-checked against virt-fw's OWN parser inside the
# tool), compose the SECOND enforcing store with it, and boot the
# leaf-signed stub. Revocation of a cert that is ALSO in db must bind:
# dbx is checked first, by definition.
REV_STORE=uefi/dist/VARS.ms-secboot-dbxrev.fd
REV_UPDATE=uefi/src/dbx-neg-signer-hash-auth2.der
echo "==> wrapping the leaf TBS-hash dbx blob into the authenticated-update header"
python3 tools/patch_carrier_negatives.py make-dbx-auth-update \
    uefi/src/dbx-neg-signer-hash.der 2020 "$REV_UPDATE"
cp "$VARS_TMPL" "$REV_STORE"
"$VIRT" --inplace "$REV_STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --add-mok "$SHIM" "$LEAF_CERT" \
    --set-dbx "$REV_UPDATE" \
    --sb > /dev/null
echo "==> revoked store shape (dbx must carry the hash-typed revocation):" && \
    "$VIRT" -i "$REV_STORE" -p 2>/dev/null | grep -E "dbx|SecureBoot" || true

echo "==> negative boot 3/4: the HASH-revoked store, the leaf-signed stub"
REV_OK=0
REV_PATH=none
boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-revoked.log 240 "$REV_STORE"
REV_STATUS=$?
echo "--- revoked-store serial (key lines) ---"
grep -a -E "Access Denied|Security Violation|FAILED|nyrqis ubld|REVOKED" \
    target/qemu-uefi-ms-secboot-revoked.log || true
echo "-----------------------------------"

# --- the SERVED SHAPE is a pinned fact, not an inference (ADR-0029) ---
# ADR-0029 makes the served dbx shape an unconditional transcript line
# precisely so a served-vs-composed mismatch or a type-GUID
# classification drift rings on its own, BEFORE any refusal is
# interpreted (a refusal that would have happened anyway must not
# certify the bytes that were read). So this leg now ASSERTS:
#   - the byte count the stub read equals the SigDB this gate composed
#     (the authenticated-update header is not part of it), and
#   - the type GUID the stub printed is EFI_CERT_X509_SHA256's
#     canonical serialized bytes {3bd2a492-96c0-4079-b420-fcf98ef103ed}
#     -- UEFI 2.10 32.4.1's wire form (field order 1-2-3-4), written
#     out here as hex rather than compared against a repo constant, so
#     a drifted constant cannot self-certify (the ADR-0028 lesson).
# The printed GUID is normalized (prefix/width/format of the stub's
# printer is not the fact; the byte sequence is).
dbx_leg_check "hash-typed" target/qemu-uefi-ms-secboot-revoked.log "$REV_UPDATE" \
    "a hash-typed entry covers the signer certificate's To-Be-Signed" \
    92a4d23bc0967940b420fcf98ef103ed "$REV_STATUS"
REV_OK=$LEG_OK
REV_PATH=$LEG_PATH
REV_SHAPE=$LEG_SHAPE
REV_EXPECT_BYTES=$LEG_EXPECT_BYTES
REV_EXPECT_GUID_HEX=$LEG_EXPECT_GUID_HEX

# --- negative 4: the ENROLLED LEAF is revoked in the WHOLE-CERTIFICATE
#     dbx form (EFI_CERT_X509, ADR-0014's byte-match rule) ---
# The hash-typed leg above pins ONE of the stub's two dbx parser
# branches. This pins the other with the same rigor, so neither branch's
# firmware-facing path rests on the host harness and the stub's embedded
# negatives alone: the leaf certificate's own DER wrapped as an
# EFI_CERT_X509 signature list (make_carrier.sh writes exactly that as
# uefi/src/dbx-neg-signer-byte.der), delivered through the
# authenticated-update header, in a THIRD enforcing store with the SAME
# enrollment -- leaf still in db AND MokList. dbx must still win.
REVC_STORE=uefi/dist/VARS.ms-secboot-dbxcert.fd
REVC_PAYLOAD=uefi/src/dbx-neg-signer-byte.der
REVC_UPDATE=uefi/src/dbx-neg-signer-byte-auth2.der
echo "==> wrapping the leaf WHOLE-CERTIFICATE dbx blob into the authenticated-update header"
python3 tools/patch_carrier_negatives.py make-dbx-auth-update \
    "$REVC_PAYLOAD" 2020 "$REVC_UPDATE"
cp "$VARS_TMPL" "$REVC_STORE"
"$VIRT" --inplace "$REVC_STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --add-mok "$SHIM" "$LEAF_CERT" \
    --set-dbx "$REVC_UPDATE" \
    --sb > /dev/null
echo "==> [whole-cert] revoked store shape (dbx must carry the byte-match entry):" && \
    "$VIRT" -i "$REVC_STORE" -p 2>/dev/null | grep -E "dbx|SecureBoot" || true

echo "==> negative boot 4/4: the WHOLE-CERT-revoked store, the leaf-signed stub"
boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-revcert.log 240 "$REVC_STORE"
REVC_STATUS=$?
echo "--- whole-cert-revoked serial (key lines) ---"
grep -a -E "Access Denied|Security Violation|FAILED|nyrqis ubld|REVOKED" \
    target/qemu-uefi-ms-secboot-revcert.log || true
echo "-------------------------------------------"
dbx_leg_check "whole-cert" target/qemu-uefi-ms-secboot-revcert.log "$REVC_UPDATE" \
    "the signer certificate byte-matches an entry" \
    a159c0a5e494a74a87b5ab155c2bf072 "$REVC_STATUS"
REVC_OK=$LEG_OK
REVC_PATH=$LEG_PATH
REVC_SHAPE=$LEG_SHAPE
REVC_EXPECT_BYTES=$LEG_EXPECT_BYTES
REVC_EXPECT_GUID_HEX=$LEG_EXPECT_GUID_HEX

# --- probe boot: a WHOLE-CERTIFICATE dbx entry read back THROUGH THE
#     STUB -- i.e. through a firmware-facing parser, not only through
#     virt-fw at composition time. ---
# The whole-cert leg's refusal happens at the boot manager, so its stub
# never runs and prints nothing; its shape evidence would otherwise be
# only the assembling tool's own read-back. This probe closes that gap
# WITHOUT weakening the refusal: it composes a store whose dbx carries
# the whole certificate of a STRANGER (the negative-only stray cert
# make_carrier.sh already generated) -- same enrollment as the other
# stores -- so the firmware accepts the leaf-signed stub, the stub runs,
# and its own dbx walk reads the entry the firmware serves, PRINTS the
# served byte count and type GUID, and (correctly) does not refuse a
# chain the entry does not cover. Asserted: exit 33 (Ready, so nothing
# refused), the served byte count equal to the composed SigDB, and the
# type GUID equal to EFI_CERT_X509's canonical serialization. Together
# with the whole-cert leg's firmware refusal, that pins the entry's
# bytes and classification from BOTH sides: the firmware refuses when it
# matches, and the stub reads it when it does not.
PROBE_STORE=uefi/dist/VARS.ms-secboot-dbxprobe.fd
PROBE_CERT_DER=uefi/tools/trust/carrier-neg-stray-cert.der
PROBE_PAYLOAD=uefi/src/dbx-probe-stranger-cert.der
PROBE_UPDATE=uefi/src/dbx-probe-stranger-cert-auth2.der
echo "==> composing the shape probe: the STRANGER certificate as an EFI_CERT_X509 dbx entry"
openssl x509 -in uefi/tools/trust/carrier-neg-stray-cert.pem -outform DER -out "$PROBE_CERT_DER"
python3 tools/patch_carrier_negatives.py make-dbx-siglist "$PROBE_CERT_DER" "$PROBE_PAYLOAD"
python3 tools/patch_carrier_negatives.py make-dbx-auth-update "$PROBE_PAYLOAD" 2020 "$PROBE_UPDATE"
cp "$VARS_TMPL" "$PROBE_STORE"
"$VIRT" --inplace "$PROBE_STORE" \
    --set-pk "$GLV" "$ROOT_CERT" \
    --add-kek "$GLV" "$ROOT_CERT" \
    --add-db "$ISD" "$ROOT_CERT" \
    --add-db "$ISD" "$LEAF_CERT" \
    --add-mok "$SHIM" "$LEAF_CERT" \
    --set-dbx "$PROBE_UPDATE" \
    --sb > /dev/null
echo "==> [probe] store shape (dbx must carry the stranger's certificate):" && \
    "$VIRT" -i "$PROBE_STORE" -p 2>/dev/null | grep -E "dbx|SecureBoot" || true

echo "==> probe boot: leaf-signed stub against a whole-cert dbx entry it does NOT match"
boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-dbxprobe.log 480 "$PROBE_STORE"
PROBE_STATUS=$?
PROBE_SERVED="$(grep -a "dbx: firmware served" target/qemu-uefi-ms-secboot-dbxprobe.log || true)"
PROBE_GUID_RAW="$(printf '%s' "$PROBE_SERVED" | sed -n 's/.*type GUID (\(.*\)).*/\1/p')"
PROBE_GUID_HEX=""
for _pb in $PROBE_GUID_RAW; do
    PROBE_GUID_HEX="${PROBE_GUID_HEX}$(printf '%02x' "$_pb")"
done
PROBE_EXPECT_BYTES=$(wc -c < "$PROBE_PAYLOAD")
PROBE_EXPECT_GUID_HEX=a159c0a5e494a74a87b5ab155c2bf072   # EFI_CERT_X509
PROBE_OK=0
if [[ "$PROBE_STATUS" -ne 33 ]]; then
    echo "FAIL: [probe] the stub did not reach Ready (status $PROBE_STATUS) -- a dbx entry for a STRANGER must not refuse this chain"
elif [[ -z "$PROBE_SERVED" ]]; then
    echo "FAIL: [probe] the stub ran but never printed the served dbx shape, so the whole-cert leg's shape evidence still rests on virt-fw alone"
else
    echo "-- [probe] served dbx shape: ${PROBE_SERVED#  }"
    echo "-- [probe] composed SigDB:   $PROBE_EXPECT_BYTES bytes, EFI_CERT_X509 GUID $PROBE_EXPECT_GUID_HEX"
    if [[ "$PROBE_SERVED" == *"firmware served $PROBE_EXPECT_BYTES signature-list bytes"* ]] \
       && [[ "$PROBE_GUID_HEX" == "$PROBE_EXPECT_GUID_HEX" ]]; then
        PROBE_OK=1
        echo "-- [probe] the stub read the composed entry through the firmware, byte count and type GUID included; Ready reached, nothing refused"
    elif [[ "$PROBE_GUID_HEX" != "$PROBE_EXPECT_GUID_HEX" ]]; then
        echo "FAIL: [probe] the served entry's type GUID is ${PROBE_GUID_HEX:-<unparsed>}, not EFI_CERT_X509 ($PROBE_EXPECT_GUID_HEX)"
    else
        echo "FAIL: [probe] the served dbx is not the $PROBE_EXPECT_BYTES-byte signature list this gate composed"
    fi
fi

# --- cross-build control (opt-in): a firmware WITHOUT the auth surface ---
# SECBOOT_CROSS_BUILD_CODE=<code image> runs the SAME store and the SAME
# package against a second firmware build and asserts the refusal there.
#
# Why this is a CONTROL and not a second acceptance measurement, measured
# 2026-10-31 on the other build in this tree (OVMF_CODE_4M.fd, sha256
# ad23261d..): that build boots with SecureBoot=absent and answers
# SetVariable with EFI_INVALID_PARAMETER (0x8000000000000002) for BOTH
# packages, the correctly-KeK-signed one included -- it has no
# time-based-auth variable surface at all. A full gate run against it
# therefore FAILS the positive leg (measured: POS_OK=0, "boot reached
# Ready but a required secboot marker line is missing"), which is the
# correct verdict for a build that cannot satisfy an enforcement claim.
# What it CAN prove is the other direction: acceptance is firmware
# support, not the host gate inventing success -- the same bytes that
# the .ms build accepts are refused by a build that lacks the surface.
# Without this leg the acceptance claim is only ever observed on one
# firmware image in the tree; with it, the claim is pinned from both
# sides. It is opt-in because it needs a second CODE image on disk.
CROSS_OK=1
CROSS_CODE="${SECBOOT_CROSS_BUILD_CODE:-}"
if [[ -n "$CROSS_CODE" ]]; then
    if [[ ! -s "$CROSS_CODE" ]]; then
        echo "FAIL: cross-build control: firmware image missing: $CROSS_CODE"
        CROSS_OK=0
    fi
    CROSS_SHA="$(sha256sum "$CROSS_CODE" 2>/dev/null | cut -d' ' -f1)"
    if [[ "$CROSS_SHA" == "$FIRMWARE_SHA" ]]; then
        echo "FAIL: cross-build control names the same image as the main run ($CROSS_CODE) -- a control against itself proves nothing"
        CROSS_OK=0
    fi
    if [[ "$CROSS_OK" -eq 1 ]]; then
        echo "==> cross-build control: the same trigger store and KeK package against $CROSS_CODE (sha256 $CROSS_SHA)"
        boot_qemu "$ESP_SIGNED" target/qemu-uefi-ms-secboot-crossbuild.log 480 "$CROSS_STORE" "$CROSS_CODE"
        CROSS_STATUS=$?
        CROSS_LOG=target/qemu-uefi-ms-secboot-crossbuild.log
        # The control's subject is the WRITE verdict, not the boot's
        # terminus. Measured 2026-10-31: the plain build runs the stub and
        # produces exactly the refusal facts below, then the KERNEL panics
        # in its own RTC wall-clock self-test (hour 22 read back as 10), so
        # QEMU times out (124) with no Ready line. Failing the control on
        # that would attribute a kernel-side signal to the firmware's auth
        # surface; silently ignoring it would hide a panic. So: the dbx
        # facts decide the control, status 33 additionally requires Ready,
        # and any other terminus is named in the transcript.
        CROSS_TERMINUS="exit $CROSS_STATUS"
        if [[ "$CROSS_STATUS" -eq 33 ]] && grep -aq "boot phase = 7 (Ready)" "$CROSS_LOG"; then
            CROSS_TERMINUS="Ready (exit 33)"
        elif [[ "$CROSS_STATUS" -ne 124 ]]; then
            echo "FAIL: cross-build control: the stub did not reach Ready and did not time out either (status $CROSS_STATUS)"
            CROSS_OK=0
        fi
        if [[ "$CROSS_OK" -eq 0 ]]; then
            :
        elif ! grep -aq "SecureBoot=" "$CROSS_LOG"; then
            echo "FAIL: cross-build control: the stub never ran on $CROSS_CODE -- no firmware-state line at all"
            CROSS_OK=0
        elif ! grep -aq "trigger present -- this store asks for the authenticated write" "$CROSS_LOG"; then
            echo "FAIL: cross-build control: the store did not ask for the authenticated write -- the control never exercised the path"
            CROSS_OK=0
        elif grep -aq "SetVariable(dbx) ACCEPTED" "$CROSS_LOG"; then
            echo "FAIL: cross-build control: $CROSS_CODE ACCEPTED the KeK package -- then this build enforces authenticated updates, and it belongs in the acceptance leg rather than the control"
            CROSS_OK=0
        elif ! grep -aq "= 0x8000000000000002 -- REFUSED by the firmware" "$CROSS_LOG"; then
            echo "FAIL: cross-build control: $CROSS_CODE refused the KeK package with a status other than the measured EFI_INVALID_PARAMETER"
            grep -a "dbx-update" "$CROSS_LOG" || true
            CROSS_OK=0
        else
            echo "-- [cross-build] $CROSS_CODE refuses the KeK package with EFI_INVALID_PARAMETER (0x8000000000000002): a build without"
            echo "   the time-based-auth surface refuses it outright, so the .ms acceptance is firmware SUPPORT measured, not the gate assuming success"
            echo "-- [cross-build] boot terminus: $CROSS_TERMINUS (the control's subject is the write verdict, not the kernel's later state)"
            if [[ "$CROSS_STATUS" -ne 33 ]]; then
                LAST_KERNEL_LINE="$(grep -a "KERNEL PANIC\|panicked at\|FAILED" "$CROSS_LOG" | tail -1 || true)"
                echo "   note: this boot did not reach Ready; kernel-side line: ${LAST_KERNEL_LINE:-<none>}"
                echo "   that is a kernel/boot-gate signal, not a cross-build acceptance fact"
            fi
        fi
    fi
fi

set -e

if [[ "$POS_OK" -eq 1 && "$NEG_OK" -eq 1 && "$TAMPER_OK" -eq 1 && "$REV_OK" -eq 1 && "$REVC_OK" -eq 1 && "$PROBE_OK" -eq 1 && "$CROSS_OK" -eq 1 ]]; then
    echo "PASS: ms OVMF under enforcement accepts the Authenticode-signed stub whose signer is chained to the enrolled carrier root, refuses both the unsigned one and the one-byte-tampered signed one, refuses the leaf-signed stub once its signer's To-Be-Signed hash is revoked BY HASH in dbx AND once its certificate is revoked in the whole-certificate dbx form, ACCEPTS a KeK-signed EFI_VARIABLE_AUTHENTICATION_2 dbx update at SetVariable time (read back byte-for-byte) while refusing its non-KeK-signed twin, and the kernel reached Ready"
    # The clause after the refusing party must match THAT path: only the
    # stub path reads the served bytes, so on the firmware path the claim
    # is the composed shape + the firmware's refusal, never "served".
    if [[ "$REV_SHAPE" == verified ]]; then
        REV_CLAIM="the stub read the composed $REV_EXPECT_BYTES-byte EFI_CERT_X509_SHA256 list (GUID $REV_EXPECT_GUID_HEX) byte-for-byte, then refused it"
    else
        REV_CLAIM="the stub never ran, so the evidence is the composed $REV_EXPECT_BYTES-byte EFI_CERT_X509_SHA256 list (GUID $REV_EXPECT_GUID_HEX) checked at composition time plus the firmware's own refusal"
    fi
    if [[ "$REVC_SHAPE" == verified ]]; then
        REVC_CLAIM="the stub read the composed $REVC_EXPECT_BYTES-byte EFI_CERT_X509 list (GUID $REVC_EXPECT_GUID_HEX) byte-for-byte, then refused it"
    else
        REVC_CLAIM="the stub never ran, so the evidence is the composed $REVC_EXPECT_BYTES-byte EFI_CERT_X509 list (GUID $REVC_EXPECT_GUID_HEX) checked at composition time plus the firmware's own refusal"
    fi
    echo "     revocation leg (hash-typed): refused by $REV_PATH; $REV_CLAIM"
    echo "     revocation leg (whole-cert): refused by $REVC_PATH; $REVC_CLAIM"
    echo "     firmware under test: $FAKECODE (sha256 $FIRMWARE_SHA)"
    echo "     dbx shape probe: $PROBE_OK (1 = the stub read the composed whole-cert entry byte-for-byte)"
    if [[ -n "$CROSS_CODE" ]]; then
        echo "     cross-build control: $CROSS_CODE (sha256 $CROSS_SHA) refuses this same package with EFI_INVALID_PARAMETER, so the acceptance above is that firmware's support, not the gate's assumption"
    fi
    exit 0
fi
echo "FAIL: ms OVMF secboot test failed (positive status $POS_STATUS marker_ok=$POS_OK, unsigned refusal_ok=$NEG_OK, tampered refusal_ok=$TAMPER_OK, hash-typed revocation ok=$REV_OK refused_by=$REV_PATH served_shape=$REV_SHAPE, whole-cert revocation ok=$REVC_OK refused_by=$REVC_PATH served_shape=$REVC_SHAPE, dbx shape probe=$PROBE_OK, cross-build control=$CROSS_OK)"
exit 1
