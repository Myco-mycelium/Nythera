#!/usr/bin/env bash
#
# verify_carrier.sh -- host-side proof of the stub's trust walk,
# WITHOUT a firmware: compiles uefi/tests/trust_selfcheck.rs (the
# stub's actual der/rsa/sha256/trust modules pulled in with #[path],
# std-only glue around them) with rustc and runs two layers of
# checks against the files make_carrier.sh just wrote:
#
#   - the GOOD carrier must VERIFY over the exact kernel image, and
#   - each of the SEVEN negative carriers must be REFUSED, with the
#     refusal naming the right step (armor expected-refusal markers
#     are passed as argument pairs below),
#   - the dbx revocation walk must refuse the chain the GOOD carrier
#     produced for each of the FIVE generated dbx blobs, and skip
#     (by name) an unknown-CertType entry and a future-dated
#     hash-typed entry,
#   - the MOK reading (mok.rs): the MOK chain verifies under the blob
#     its MokList carries, refuses under the embedded anchor and under
#     a stranger MokList blob, and is revoked by its own dbx hash blob.
#
# Called by build.sh right after make_carrier.sh, so a broken signing
# chain or a dead refusal path fails the BUILD, never a boot. The
# walk's clock is the host's own UTC time (std::time in the harness,
# printed for cross-checking) -- the firmware's GetTime plays this
# role at boot.
#
# Usage: ./tools/verify_carrier.sh <kernel-elf>

set -euo pipefail
cd "$(dirname "$0")/.."

KERNEL="${1:?usage: verify_carrier.sh <kernel-elf>}"
if [[ ! -s "$KERNEL" ]]; then
    echo "verify_carrier.sh: kernel image missing or empty: $KERNEL"
    exit 1
fi
for f in uefi/src/carrier.der uefi/src/trusted-root.der \
         uefi/src/carrier-neg-anchor.der uefi/src/carrier-neg-digest.der \
         uefi/src/carrier-neg-sid.der uefi/src/carrier-neg-attrs.der \
         uefi/src/carrier-neg-window-expired.der uefi/src/carrier-neg-window-future.der \
         uefi/src/carrier-neg-detached.der \
         uefi/src/dbx-neg-signer-byte.der uefi/src/dbx-neg-signer-pair.der \
         uefi/src/dbx-neg-anchor-byte.der \
         uefi/src/dbx-neg-signer-hash.der uefi/src/dbx-neg-anchor-hash.der \
         uefi/src/mok-carrier.der uefi/src/moklist-pos.der \
         uefi/src/moklist-neg-stranger.der uefi/src/dbx-neg-mok-hash.der; do
    if [[ ! -s "$f" ]]; then
        echo "verify_carrier.sh: missing carrier-set file (run make_carrier.sh first): $f"
        exit 1
    fi
done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

rustc --edition 2021 -O -o "$TMP/trust_selfcheck" uefi/tests/trust_selfcheck.rs \
    2>"$TMP/rustc.log" || { cat "$TMP/rustc.log"; exit 1; }

"$TMP/trust_selfcheck" \
    uefi/src/carrier.der \
    uefi/src/trusted-root.der \
    "$KERNEL" \
    uefi/src/carrier-neg-anchor.der \
    "carrier: the embedded signer certificate is not the anchor and does not verify under the anchor's key" \
    uefi/src/carrier-neg-digest.der \
    "rsa: DigestInfo digest differs from the message digest" \
    uefi/src/carrier-neg-sid.der \
    "carrier: the SignerIdentifier's serial does not byte-equal the signer certificate's serialNumber" \
    uefi/src/carrier-neg-attrs.der \
    "signerInfo: signedAttrs present -- the stub walks -noattr carriers only" \
    uefi/src/carrier-neg-window-expired.der \
    "cert: the leaf's validity window does not include the boot clock (expired at its notAfter)" \
    uefi/src/carrier-neg-window-future.der \
    "cert: the leaf's validity window does not include the boot clock (not valid before its notBefore)" \
    uefi/src/carrier-neg-detached.der \
    "encapContentInfo: eContent present -- the stub walks DETACHED carriers only" \
    --dbx \
    uefi/src/dbx-neg-signer-byte.der \
    "dbx: the carrier chain is REVOKED by the firmware's dbx (the signer certificate byte-matches an entry)" \
    uefi/src/dbx-neg-signer-pair.der \
    "dbx: the carrier chain is REVOKED by the firmware's dbx (the signer's issuer+serial matches an entry)" \
    uefi/src/dbx-neg-anchor-byte.der \
    "dbx: the carrier chain is REVOKED by the firmware's dbx (the anchor certificate byte-matches an entry)" \
    uefi/src/dbx-neg-signer-hash.der \
    "dbx: the carrier chain is REVOKED by the firmware's dbx (a hash-typed entry covers the signer certificate's To-Be-Signed)" \
    uefi/src/dbx-neg-anchor-hash.der \
    "dbx: the carrier chain is REVOKED by the firmware's dbx (a hash-typed entry covers the anchor certificate's To-Be-Signed)" \
    --mok \
    uefi/src/moklist-pos.der \
    uefi/src/mok-carrier.der \
    uefi/src/moklist-neg-stranger.der \
    uefi/src/dbx-neg-mok-hash.der
