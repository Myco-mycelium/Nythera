#!/usr/bin/env python3
"""nyrqisctl package — Create, sign, and inspect .nypkg packages.

Implements NPS-026 package creation workflow.

Usage:
    nyrqisctl package create --dir ./my-app --manifest manifest.json --key key.json --output my-app.nypkg
    nyrqisctl package inspect --dir my-app.nypkg
    nyrqisctl package verify --dir my-app.nypkg --trust-store trust.json

Examples:
    # Create a manifest
    cat > manifest.json << 'EOF'
    {
      "name": "my-app",
      "version": "1.0.0",
      "publisher": "My Company",
      "runtime_class": "native",
      "capabilities": ["CAP_FS_READ", "CAP_NETWORK"],
      "images": ["base"]
    }
    EOF

    # Generate a signing key
    nyrqisctl sign generate-key --output publisher-key.json

    # Package and sign
    nyrqisctl package create \\
        --dir ./my-app \\
        --manifest manifest.json \\
        --key publisher-key.json \\
        --output my-app.nypkg

    # Inspect a package
    nyrqisctl package inspect --dir my-app.nypkg

    # Verify a package
    nyrqisctl package verify --dir my-app.nypkg --trust-store trust.json

    # Install a package
    nyrqisctl package install --dir my-app.nypkg --trust-store trust.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def _compute_file_hash(path: Path) -> bytes:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.digest()


def _build_merkle_root(hashes: dict[str, bytes]) -> bytes:
    """Build a Merkle root from file hashes."""
    if not hashes:
        return b"\x00" * 32
    # Simple concatenation + hash for now
    h = hashlib.sha256()
    for name in sorted(hashes.keys()):
        h.update(name.encode())
        h.update(hashes[name])
    return h.digest()


def cmd_create(args):
    """Create a signed .nypkg package."""
    from backend.package_signing import SigningKeypair, sign_package, PackageSignature

    # Load manifest
    manifest_data = json.loads(Path(args.manifest).read_text())

    # Scan files
    pkg_dir = Path(args.dir)
    if not pkg_dir.is_dir():
        print(f"Error: {args.dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Compute file hashes
    file_hashes = {}
    for root, dirs, files in os.walk(pkg_dir):
        for fname in files:
            fpath = Path(root) / fname
            rel_path = str(fpath.relative_to(pkg_dir))
            file_hashes[rel_path] = _compute_file_hash(fpath)

    if not file_hashes:
        print("Error: no files found in package directory", file=sys.stderr)
        sys.exit(1)

    # Build integrity tree
    root_hash = _build_merkle_root(file_hashes)

    # Create output package
    output = Path(args.output) if args.output else Path(f"{manifest_data['name']}-{manifest_data['version']}.nypkg")
    output.mkdir(parents=True, exist_ok=True)

    # Copy files
    import shutil
    images_dir = output / "images"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    shutil.copytree(pkg_dir, images_dir)

    # Write manifest (compact format to match signing serialization)
    (output / "manifest.json").write_text(json.dumps(manifest_data, sort_keys=True, separators=(",", ":")))

    # Write integrity tree
    import base64
    tree_data = {
        "root_hash": base64.b64encode(root_hash).decode(),
        "file_hashes": {
            k: base64.b64encode(v).decode()
            for k, v in file_hashes.items()
        },
    }
    (output / "integrity.json").write_text(json.dumps(tree_data, indent=2))

    # Sign the package
    if args.key:
        key_data = json.loads(Path(args.key).read_text())
        if "private_key" not in key_data:
            print("Error: key file does not contain a private key", file=sys.stderr)
            sys.exit(1)

        private_key = base64.b64decode(key_data["private_key"])
        kp = SigningKeypair.from_private_key(private_key)

        manifest_bytes = json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode()
        tree_bytes = root_hash

        sig_bytes = sign_package(manifest_bytes, tree_bytes, kp)
        sig_block = PackageSignature(
            public_key=kp.public_key,
            signature=sig_bytes,
            key_id=kp.key_id,
        )
        (output / "signature.json").write_text(json.dumps(sig_block.to_dict(), indent=2))
        print(f"✅ Package signed with key {kp.key_id}")
    else:
        print("⚠️  Package is NOT signed (NPS-026 §6.1 requires signing)")

    print(f"📦 Package created: {output}")
    print(f"   Files: {len(file_hashes)}")
    print(f"   Root hash: {root_hash.hex()[:16]}...")


def cmd_inspect(args):
    """Inspect a .nypkg package."""
    pkg_dir = Path(args.dir)
    if not pkg_dir.is_dir():
        print(f"Error: {args.dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Load manifest
    manifest_path = pkg_dir / "manifest.json"
    if not manifest_path.exists():
        print("Error: package missing manifest.json", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text())
    print("📋 Package Manifest:")
    print(f"   Name: {manifest.get('name')}")
    print(f"   Version: {manifest.get('version')}")
    print(f"   Publisher: {manifest.get('publisher')}")
    print(f"   Runtime: {manifest.get('runtime_class', 'native')}")
    print(f"   Capabilities: {manifest.get('capabilities', [])}")

    # Check signature
    sig_path = pkg_dir / "signature.json"
    if sig_path.exists():
        sig_data = json.loads(sig_path.read_text())
        print(f"\n🔐 Signature:")
        print(f"   Key ID: {sig_data.get('key_id')}")
        print(f"   Signed: Yes")
    else:
        print(f"\n🔐 Signature: NOT SIGNED")

    # Check integrity
    tree_path = pkg_dir / "integrity.json"
    if tree_path.exists():
        tree_data = json.loads(tree_path.read_text())
        file_count = len(tree_data.get("file_hashes", {}))
        print(f"\n🌳 Integrity Tree:")
        print(f"   Files: {file_count}")
        print(f"   Root hash: {tree_data.get('root_hash', '')[:16]}...")
    else:
        print(f"\n🌳 Integrity Tree: NOT FOUND")


def cmd_verify(args):
    """Verify a package signature."""
    from backend.package_signing import verify_package, PackageSignature, TrustStore

    pkg_dir = Path(args.dir)

    # Load signature
    sig_path = pkg_dir / "signature.json"
    if not sig_path.exists():
        print("❌ Package is not signed", file=sys.stderr)
        sys.exit(1)

    sig_data = json.loads(sig_path.read_text())
    sig_block = PackageSignature.from_dict(sig_data)

    # Load manifest and tree
    manifest_data = json.loads((pkg_dir / "manifest.json").read_text())
    manifest_bytes = json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode()
    tree_data = json.loads((pkg_dir / "integrity.json").read_text())
    import base64
    tree_bytes = base64.b64decode(tree_data["root_hash"])

    # Verify signature
    try:
        valid = verify_package(manifest_bytes, tree_bytes, sig_block.signature, sig_block.public_key)
        if valid:
            print(f"✅ Signature is VALID (key_id={sig_block.key_id})")
        else:
            print(f"❌ Signature is INVALID")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Verification failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Check trust
    if args.trust_store:
        store = TrustStore.load(args.trust_store)
        if store.verify_against_trust(sig_block):
            print(f"✅ Key is TRUSTED")
        else:
            print(f"⚠️  Key is NOT TRUSTED (publisher: {sig_block.key_id})")
            sys.exit(1)


def cmd_install(args):
    """Install a .nypkg package."""
    from backend.installer import PackageInstaller

    installer = PackageInstaller(
        install_dir=args.install_dir or "/var/lib/nyrqis/packages",
        trust_store_path=args.trust_store,
    )

    try:
        result = installer.install(args.dir)
        print(f"✅ Installed {result['name']} v{result['version']}")
        print(f"   Publisher: {result.get('publisher')}")
        print(f"   Key ID: {result.get('key_id')}")
        print(f"   Path: {result.get('path')}")
    except Exception as e:
        print(f"❌ Installation failed: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="nyrqisctl package",
        description="Create, sign, and inspect .nypkg packages (NPS-026)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # create
    create_parser = subparsers.add_parser("create", help="Create a signed package")
    create_parser.add_argument("--dir", "-d", required=True, help="Source directory with files")
    create_parser.add_argument("--manifest", "-m", required=True, help="Manifest JSON file")
    create_parser.add_argument("--key", "-k", help="Signing key file")
    create_parser.add_argument("--output", "-o", help="Output package directory")

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a package")
    inspect_parser.add_argument("--dir", "-d", required=True, help="Package directory")

    # verify
    verify_parser = subparsers.add_parser("verify", help="Verify package signature")
    verify_parser.add_argument("--dir", "-d", required=True, help="Package directory")
    verify_parser.add_argument("--trust-store", help="Trust store file")

    # install
    install_parser = subparsers.add_parser("install", help="Install a package")
    install_parser.add_argument("--dir", "-d", required=True, help="Package directory")
    install_parser.add_argument("--trust-store", help="Trust store file")
    install_parser.add_argument("--install-dir", help="Installation base directory")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "install":
        cmd_install(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
