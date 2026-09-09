#!/usr/bin/env python3
"""nyrqisctl repo — Manage a signed Nyrqis package repository.

The operator front-end for ``backend/package_repo.py`` (NPS-026 §7
repository management): publish packages and signed delta updates,
and query/verify the repository's signed index as a client.

Usage:
    nyrqisctl_repo publish-package --repo /srv/nypkg --dir my-app.nypkg \
        --id my-app --version 1.0.0 --key publisher-key.json
    nyrqisctl_repo publish-delta --repo /srv/nypkg --old-dir v1 --new-dir v2 \
        --id my-app --from 1.0.0 --to 1.1.0 --key publisher-key.json
    nyrqisctl_repo list --repo /srv/nypkg --trust-store trust.json
    nyrqisctl_repo find --repo /srv/nypkg --id my-app --version 1.0.0 \
        --trust-store trust.json
    nyrqisctl_repo find-delta --repo /srv/nypkg --id my-app \
        --from 1.0.0 --to 1.1.0 --trust-store trust.json
    nyrqisctl_repo verify-entry --repo /srv/nypkg --id my-app \
        --version 1.0.0 --trust-store trust.json
    nyrqisctl_repo remove --repo /srv/nypkg --id my-app --version 1.0.0 \
        --key publisher-key.json

Examples:
    # Generate a publisher key (see nyrqisctl sign generate-key)
    nyrqisctl sign generate-key --output repo-key.json

    # Publish a package version
    nyrqisctl_repo publish-package \
        --repo /srv/nypkg --dir ./my-app.nypkg \
        --id my-app --version 1.0.0 --key repo-key.json

    # Publish a signed delta between two payload trees
    nyrqisctl_repo publish-delta \
        --repo /srv/nypkg --old-dir ./my-app-1.0.0 --new-dir ./my-app-1.1.0 \
        --id my-app --from 1.0.0 --to 1.1.0 --key repo-key.json

    # Client side: verify the index, then locate and check an entry
    nyrqisctl_repo list --repo /srv/nypkg --trust-store trust.json
    nyrqisctl_repo verify-entry --repo /srv/nypkg --id my-app \
        --version 1.0.0 --trust-store trust.json

Trust model: every client-side command verifies the index signature
against the trust store first (fail-closed — an unsigned index, an
untrusted key, or a tampered entry aborts the command with exit 1).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_keypair(key_path: str):
    """Load a signing keypair from a key file (nyrqisctl sign format)."""
    import base64

    from backend.package_signing import SigningKeypair

    key_data = json.loads(Path(key_path).read_text())
    if "private_key" not in key_data:
        print(
            "Error: key file does not contain a private key", file=sys.stderr
        )
        sys.exit(1)
    private_key = base64.b64decode(key_data["private_key"])
    try:
        return SigningKeypair.from_private_key(private_key)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: cannot load signing key: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_publish_package(args) -> int:
    """Publish a package version into the repository."""
    from backend.package_repo import PackageRepository, RepoError

    kp = _load_keypair(args.key)
    repo = PackageRepository(args.repo)
    try:
        entry = repo.publish_package(
            args.dir, args.id, args.version, kp
        )
    except RepoError as exc:
        print(f"❌ Publish failed: {exc}", file=sys.stderr)
        return 1
    print(f"📦 Published {args.id} v{args.version}")
    print(f"   Checksum: {entry['checksum'][:16]}…")
    print(f"   Index re-signed with key {kp.key_id}")
    return 0


def cmd_publish_delta(args) -> int:
    """Generate a signed delta between two payload trees and publish it."""
    from backend.delta_update import (
        create_delta_update, delta_payload_bytes, save_delta_update,
    )
    from backend.package_repo import PackageRepository, RepoError

    kp = _load_keypair(args.key)
    repo = PackageRepository(args.repo)

    delta = create_delta_update(
        args.old_dir, args.new_dir,
        args.id, args.version_from, args.version_to,
        signing_keypair=kp,
    )
    if not delta.get("signature"):
        print(
            "Error: delta was not signed (unusable for publication)",
            file=sys.stderr,
        )
        return 1

    deltas_dir = Path(args.repo) / "deltas"
    doc_path = deltas_dir / args.id / (
        f"{args.version_from}_{args.version_to}.json"
    )
    payload_path = deltas_dir / args.id / (
        f"{args.version_from}_{args.version_to}.delta"
    )
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    save_delta_update(delta, str(doc_path))
    payload_path.write_bytes(delta_payload_bytes(delta))

    try:
        entry = repo.publish_delta(delta, str(payload_path), kp)
    except RepoError as exc:
        print(f"❌ Publish failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"🧩 Published delta {args.id} {args.version_from} → "
        f"{args.version_to}"
    )
    print(f"   Ops: {len(delta.get('ops', []))}")
    print(f"   Checksum: {entry['checksum'][:16]}…")
    print(f"   Delta doc: {doc_path}")
    print(f"   Index re-signed with key {kp.key_id}")
    return 0


def _verified_index(repo, trust_store: str):
    from backend.package_repo import RepoError

    try:
        return repo.load_index(trust_store)
    except RepoError as exc:
        print(f"❌ Index verification failed: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args) -> int:
    """Verify the index and list its entries."""
    from backend.package_repo import PackageRepository

    repo = PackageRepository(args.repo)
    index = _verified_index(repo, args.trust_store)
    packages = [
        e for e in index.get("packages", []) if e.get("type") == "package"
    ]
    deltas = [
        e for e in index.get("packages", []) if e.get("type") == "delta"
    ]
    print(f"🗂️  Repository index VERIFIED ({len(packages)} packages, "
          f"{len(deltas)} deltas)")
    for e in packages:
        print(f"   📦 {e['package_id']} v{e['version']}  "
              f"({e['checksum'][:12]}…)")
    for e in deltas:
        print(f"   🧩 {e['package_id']} {e['version_from']} → "
              f"{e['version_to']}  ({e['checksum'][:12]}…)")
    return 0


def cmd_find(args) -> int:
    """Find a verified package entry."""
    from backend.package_repo import PackageRepository

    repo = PackageRepository(args.repo)
    entry = repo.find(args.id, args.version, args.trust_store)
    if entry is None:
        print(f"❌ Not found: {args.id} v{args.version}", file=sys.stderr)
        return 1
    print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


def cmd_find_delta(args) -> int:
    """Find a verified delta entry between two versions."""
    from backend.package_repo import PackageRepository

    repo = PackageRepository(args.repo)
    entry = repo.find_delta(
        args.id, args.version_from, args.version_to, args.trust_store
    )
    if entry is None:
        print(
            f"❌ No delta: {args.id} {args.version_from} → "
            f"{args.version_to}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


def cmd_verify_entry(args) -> int:
    """Verify an entry's on-disk payload against its signed checksum."""
    from backend.package_repo import PackageRepository

    repo = PackageRepository(args.repo)
    if args.version_from:
        if not args.version_to:
            print("Error: --to is required with --from (delta entries)",
                  file=sys.stderr)
            return 2
        entry = repo.find_delta(
            args.id, args.version_from, args.version_to,
            args.trust_store,
        )
        label = f"delta {args.id} {args.version_from} → {args.version_to}"
    elif not args.version:
        print("Error: --version is required without --from "
              "(package entries)", file=sys.stderr)
        return 2
    else:
        entry = repo.find(args.id, args.version, args.trust_store)
        label = f"{args.id} v{args.version}"
    if entry is None:
        print(f"❌ Not found: {label}", file=sys.stderr)
        return 1
    if not repo.verify_entry_content(entry):
        print(f"❌ Content check FAILED for {label} (tamper suspected)",
              file=sys.stderr)
        return 1
    print(f"✅ {label}: content matches signed checksum")
    return 0


def cmd_remove(args) -> int:
    """Remove a package version from the repository."""
    from backend.package_repo import PackageRepository, RepoError

    kp = _load_keypair(args.key)
    repo = PackageRepository(args.repo)
    try:
        existed = repo.remove_package(args.id, args.version, kp)
    except RepoError as exc:
        print(f"❌ Remove failed: {exc}", file=sys.stderr)
        return 1
    if existed:
        print(f"🗑️  Removed {args.id} v{args.version} (index re-signed)")
    else:
        print(f"⚠️  {args.id} v{args.version} was not present")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="nyrqisctl repo",
        description="Manage a signed Nyrqis package repository "
                    "(NPS-026 §7)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "publish-package", help="Publish a package version")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--dir", "-d", required=True,
                   help="Package payload directory (.nypkg)")
    p.add_argument("--id", required=True, help="Package id")
    p.add_argument("--version", required=True, help="Package version")
    p.add_argument("--key", "-k", required=True, help="Signing key file")
    p.set_defaults(func=cmd_publish_package)

    p = sub.add_parser(
        "publish-delta",
        help="Generate + publish a signed delta update")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--old-dir", required=True,
                   help="Base version payload directory")
    p.add_argument("--new-dir", required=True,
                   help="Target version payload directory")
    p.add_argument("--id", required=True, help="Package id")
    p.add_argument("--from", dest="version_from", required=True,
                   help="Base version")
    p.add_argument("--to", dest="version_to", required=True,
                   help="Target version")
    p.add_argument("--key", "-k", required=True, help="Signing key file")
    p.set_defaults(func=cmd_publish_delta)

    p = sub.add_parser(
        "list", help="Verify the index and list entries")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--trust-store", "-t", required=True,
                   help="Trust store file")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("find", help="Find a package entry (verified index)")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--id", required=True, help="Package id")
    p.add_argument("--version", required=True, help="Package version")
    p.add_argument("--trust-store", "-t", required=True,
                   help="Trust store file")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser(
        "find-delta", help="Find a delta entry (verified index)")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--id", required=True, help="Package id")
    p.add_argument("--from", dest="version_from", required=True,
                   help="Base version")
    p.add_argument("--to", dest="version_to", required=True,
                   help="Target version")
    p.add_argument("--trust-store", "-t", required=True,
                   help="Trust store file")
    p.set_defaults(func=cmd_find_delta)

    p = sub.add_parser(
        "verify-entry",
        help="Verify an entry's payload against its signed checksum")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--id", required=True, help="Package id")
    p.add_argument("--version", help="Package version (package entries)")
    p.add_argument("--from", dest="version_from",
                   help="Base version (delta entries)")
    p.add_argument("--to", dest="version_to",
                   help="Target version (delta entries)")
    p.add_argument("--trust-store", "-t", required=True,
                   help="Trust store file")
    p.set_defaults(func=cmd_verify_entry)

    p = sub.add_parser(
        "remove", help="Remove a package version (re-signs the index)")
    p.add_argument("--repo", "-r", required=True, help="Repository root")
    p.add_argument("--id", required=True, help="Package id")
    p.add_argument("--version", required=True, help="Package version")
    p.add_argument("--key", "-k", required=True, help="Signing key file")
    p.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
