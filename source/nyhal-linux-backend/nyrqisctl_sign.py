#!/usr/bin/env python3
"""nyrqisctl sign — Sign and verify .nypkg packages.

Implements NPS-026 §6 (Digital Signatures) CLI interface.

Usage:
    nyrqisctl sign generate-key --output key.json
    nyrqisctl sign sign --manifest manifest.json --tree tree.json --key key.json --output sig.json
    nyrqisctl sign verify --manifest manifest.json --tree tree.json --sig sig.json
    nyrqisctl sign trust --add public_key.json
    nyrqisctl sign trust --remove key_id
    nyrqisctl sign trust --list

Examples:
    # Generate a signing keypair
    nyrqisctl sign generate-key --output publisher-key.json

    # Sign a package
    nyrqisctl sign sign \\
        --manifest package-manifest.json \\
        --tree integrity-tree.json \\
        --key publisher-key.json \\
        --output package.sig

    # Verify a package signature
    nyrqisctl sign verify \\
        --manifest package-manifest.json \\
        --tree integrity-tree.json \\
        --sig package.sig

    # Manage trust store
    nyrqisctl sign trust --list
    nyrqisctl sign trust --add trusted-key.json
    nyrqisctl sign trust --remove abc12345
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_generate_key(args):
    """Generate a new signing keypair."""
    from backend.package_signing import SigningKeypair

    kp = SigningKeypair.generate()

    if args.output:
        # Save the full keypair (private + public)
        data = kp.private_dict()
        Path(args.output).write_text(json.dumps(data, indent=2))
        print(f"Keypair generated and saved to {args.output}")
        print(f"  Key ID: {kp.key_id}")
        print(f"  Public key: {len(kp.public_key)} bytes")
        print(f"  ⚠️  Keep the private key secure — it cannot be recovered")
    else:
        # Print to stdout
        data = kp.private_dict()
        print(json.dumps(data, indent=2))


def cmd_sign(args):
    """Sign a package."""
    from backend.package_signing import SigningKeypair, sign_package, PackageSignature

    # Load the signing key
    key_data = json.loads(Path(args.key).read_text())
    if "private_key" not in key_data:
        print("Error: key file does not contain a private key", file=sys.stderr)
        sys.exit(1)

    import base64
    private_key = base64.b64decode(key_data["private_key"])
    kp = SigningKeypair.from_private_key(private_key)

    # Load manifest and integrity tree
    manifest_bytes = Path(args.manifest).read_bytes()
    tree_bytes = Path(args.tree).read_bytes()

    # Sign
    sig_bytes = sign_package(manifest_bytes, tree_bytes, kp)

    # Create signature block
    sig_block = PackageSignature(
        public_key=kp.public_key,
        signature=sig_bytes,
        key_id=kp.key_id,
    )

    if args.output:
        # Save as JSON
        data = sig_block.to_dict()
        data["manifest"] = args.manifest
        data["tree"] = args.tree
        Path(args.output).write_text(json.dumps(data, indent=2))
        print(f"Package signed and saved to {args.output}")
        print(f"  Key ID: {kp.key_id}")
        print(f"  Signature: {len(sig_bytes)} bytes")
    else:
        print(json.dumps(sig_block.to_dict(), indent=2))


def cmd_verify(args):
    """Verify a package signature."""
    from backend.package_signing import verify_package, PackageSignature

    # Load signature block
    sig_data = json.loads(Path(args.sig).read_text())
    sig_block = PackageSignature.from_dict(sig_data)

    # Load manifest and integrity tree
    manifest_bytes = Path(args.manifest).read_bytes()
    tree_bytes = Path(args.tree).read_bytes()

    # Verify
    try:
        result = verify_package(
            manifest_bytes, tree_bytes,
            sig_block.signature, sig_block.public_key,
        )
        if result:
            print(f"✅ Signature is VALID")
            print(f"  Key ID: {sig_block.key_id}")
        else:
            print(f"❌ Signature is INVALID")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Verification failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_trust(args):
    """Manage the trust store."""
    from backend.package_signing import TrustStore, SigningKeypair

    trust_path = args.trust_store or "trust-store.json"

    # Load existing store
    if Path(trust_path).exists():
        store = TrustStore.load(trust_path)
    else:
        store = TrustStore()

    if args.add:
        # Add a trusted key
        key_data = json.loads(Path(args.add).read_text())
        import base64
        public_key = base64.b64decode(key_data["public_key"])
        key_id = store.add_trusted(public_key)
        store.save(trust_path)
        print(f"Added trusted key: {key_id}")
    elif args.remove:
        # Remove a trusted key
        result = store.remove_trusted(args.remove)
        if result:
            store.save(trust_path)
            print(f"Removed trusted key: {args.remove}")
        else:
            print(f"Key not found: {args.remove}")
            sys.exit(1)
    elif args.list:
        # List trusted keys
        keys = store.list_trusted()
        if keys:
            print(f"Trusted keys ({len(keys)}):")
            for kid in keys:
                print(f"  {kid}")
        else:
            print("No trusted keys")
    else:
        print("Specify --add, --remove, or --list", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="nyrqisctl sign",
        description="Sign and verify .nypkg packages (NPS-026 §6)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # generate-key
    gen_parser = subparsers.add_parser("generate-key", help="Generate a signing keypair")
    gen_parser.add_argument("--output", "-o", help="Output file for the keypair")

    # sign
    sign_parser = subparsers.add_parser("sign", help="Sign a package")
    sign_parser.add_argument("--manifest", "-m", required=True, help="Manifest file")
    sign_parser.add_argument("--tree", "-t", required=True, help="Integrity tree file")
    sign_parser.add_argument("--key", "-k", required=True, help="Signing key file")
    sign_parser.add_argument("--output", "-o", help="Output signature file")

    # verify
    verify_parser = subparsers.add_parser("verify", help="Verify a package signature")
    verify_parser.add_argument("--manifest", "-m", required=True, help="Manifest file")
    verify_parser.add_argument("--tree", "-t", required=True, help="Integrity tree file")
    verify_parser.add_argument("--sig", "-s", required=True, help="Signature file")

    # trust
    trust_parser = subparsers.add_parser("trust", help="Manage trust store")
    trust_parser.add_argument("--add", help="Add a trusted public key file")
    trust_parser.add_argument("--remove", help="Remove a trusted key by ID")
    trust_parser.add_argument("--list", action="store_true", help="List trusted keys")
    trust_parser.add_argument("--trust-store", help="Trust store file path")

    args = parser.parse_args()

    if args.command == "generate-key":
        cmd_generate_key(args)
    elif args.command == "sign":
        cmd_sign(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "trust":
        cmd_trust(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
