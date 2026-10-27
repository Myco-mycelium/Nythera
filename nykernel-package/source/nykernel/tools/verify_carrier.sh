#!/usr/bin/env bash
#
# verify_carrier.sh -- host-side proof of the stub's trust walk,
# WITHOUT a firmware: compiles uefi/tests/trust_selfcheck.rs (the
# stub's actual der/rsa/sha256/trust modules pulled in with #[path],
# std-only glue around them) with rustc and runs two layers of
# checks against the files make_carrier.sh just wrote:
#
#   - the GOOD carrier must VERIFY over the exact kernel image, and
#   - each of the FIVE negative carriers must be REFUSED, with the
#     refusal naming the right step (armor expected-refusal markers
#     are passed as argument pairs below).
#
# Called by build.sh right after make_carrier.sh, so a broken signing
# chain or a dead refusal path fails the BUILD, never a boot.
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
         uefi/src/carrier-neg-detached.der; do
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
    uefi/src/carrier-neg-detached.der \
    "encapContentInfo: eContent present -- the stub walks DETACHED carriers only"
