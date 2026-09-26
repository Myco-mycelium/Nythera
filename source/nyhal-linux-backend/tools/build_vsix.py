#!/usr/bin/env python3
"""Build the vscode-nyrqis extension into an installable .vsix.

The IDE-integration roadmap item landed as a prototype with the
packaging half open because a marketplace package "can't be built
here" (no vsce, no npm registry access). The BUILD half, however,
needs neither: a .vsix is a zip containing the extension files under
``extension/`` plus two VSIX-structure members at the archive root
(``extension.vsixmanifest``, per the VSIX package format §4, and
``[Content_Types].xml``, §5). This script assembles exactly that with
the Python standard library alone — no vsce, no network, no node.

Discipline:
- Fail-closed: a manifest missing required fields (name, version,
  publisher, engines.vscode, main), pointing ``main`` at a file that
  does not exist on disk, or yielding an empty package refuses with
  exit 1 and builds nothing.
- The mock-harness ``node_modules/`` stub and dotfiles are never
  packaged.
- Deterministic bytes: every zip member gets a fixed timestamp and the
  manifest's ``CreationDate`` comes from ``SOURCE_DATE_EPOCH``
  (the reproducible-builds standard) with a fixed epoch default — so
  two runs over the same tree produce byte-identical archives (same
  sha256) no matter how far apart in time they run. The first draft
  used wall-clock ``CreationDate`` and its determinism claim was
  false across second boundaries; caught by re-probing the claim, not
  by the original test (which built twice inside the same second).
  A rebuild can be hash-checked instead of trusted.
- Self-verifying: the written archive is reopened and must contain the
  entry point and the manifest before the build is reported.

Usage:
    python3 tools/build_vsix.py [--extension-dir DIR] [--out FILE]

Defaults: extension dir ``<backend>/ide/vscode-nyrqis``, output
``<extension-dir>/vscode-nyrqis-<version>.vsix``. Prints the packaged
file list, size, and sha256 on success.
"""

import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_EXT_DIR = os.path.join(_BACKEND, "ide", "vscode-nyrqis")

# Fixed member timestamp (zip's floor) for byte-identical rebuilds.
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)

_REQUIRED_FIELDS = ("name", "version", "publisher", "main")
_CONTENT_TYPES = b"""<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
  <Default Extension="xml" ContentType="text/xml"/>
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="md" ContentType="text/markdown"/>
</Types>
"""


class BuildError(Exception):
    """A fail-closed refusal — nothing is written."""


def _fail(msg: str) -> "BuildError":
    return BuildError(msg)


def _load_manifest(ext_dir: str) -> dict:
    path = os.path.join(ext_dir, "package.json")
    if not os.path.isfile(path):
        raise _fail(f"no package.json at {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(f"package.json unreadable: {exc}") from exc
    missing = [f for f in _REQUIRED_FIELDS if not manifest.get(f)]
    if missing:
        raise _fail(f"package.json missing required fields: {missing}")
    if not (manifest.get("engines") or {}).get("vscode"):
        raise _fail("package.json missing engines.vscode")
    return manifest


def _collect_files(ext_dir: str) -> list:
    """Every packageable file under ext_dir, POSIX-relative, sorted.

    Excluded: node_modules (the mock-harness stub lives there), any
    dotfile/dotdir, existing .vsix artifacts.
    """
    files = []
    for root, dirs, names in os.walk(ext_dir):
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and d != "node_modules")
        for name in sorted(names):
            if name.startswith(".") or name.endswith(".vsix"):
                continue
            full = os.path.join(root, name)
            if os.path.isfile(full):
                rel = os.path.relpath(full, ext_dir).replace(os.sep, "/")
                files.append(rel)
    if not files:
        raise _fail("extension directory yields no packageable files")
    return files


def _creation_date() -> str:
    """VSIX CreationDate, reproducibly: SOURCE_DATE_EPOCH if set (the
    reproducible-builds convention), else the epoch itself — never
    wall-clock, which would break byte-identical rebuilds."""
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _vsixmanifest(manifest: dict) -> bytes:
    now = _creation_date()
    identity = f"{manifest['publisher']}.{manifest['name']}"
    props = [
        ("Microsoft.VisualStudio.Code.Engine",
         manifest["engines"]["vscode"]),
        ("Microsoft.VisualStudio.Code.ExtensionDependencies",
         json.dumps(manifest.get("extensionDependencies", []))),
        ("Microsoft.VisualStudio.Code.ExtensionPack",
         json.dumps(manifest.get("extensionPack", []))),
        ("Microsoft.VisualStudio.Code.LocalizedLanguages",
         json.dumps(manifest.get("l10n", []))),
    ]
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        ('<PackageManifest Version="2.0.0" xmlns='
         '"http://schemas.microsoft.com/developer/vsx-schema/2011" '
         'xmlns:d='
         '"http://schemas.microsoft.com/developer/vsx-schema-design/2011">'),
        "  <Metadata>",
        f'    <Identity Language="en-US" Id="{identity}" '
        f'Version="{manifest["version"]}"/>',
        f'    <DisplayName>{manifest.get("displayName", manifest["name"])}'
        "</DisplayName>",
        f'    <Description xml:space="preserve">'
        f'{manifest.get("description", "")}</Description>',
        f'    <Categories>{",".join(manifest.get("categories", []))}'
        "</Categories>",
        (f'    <Properties>\n' +
         "".join(
             f'      <Property Id="{pid}" Value="{val}"/>\n'
             for pid, val in props) +
         "    </Properties>"),
        "  </Metadata>",
        "  <Installation>",
        (f'    <InstallationTarget Id="Microsoft.VisualStudio.Code"'
         f'/>'),
        "  </Installation>",
        "  <Assets>",
        (f'    <Asset Type="Microsoft.VisualStudio.Code.Manifest" '
         f'Path="extension/package.json" Addressable="true"/>'),
        "  </Assets>",
        f'  <CreationDate>{now}</CreationDate>',
        "</PackageManifest>",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _zinfo(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def build_vsix(ext_dir: str, out_path: str) -> dict:
    """Assemble the .vsix; returns a report dict. Fail-closed."""
    manifest = _load_manifest(ext_dir)
    main = manifest["main"]
    if not os.path.isfile(os.path.join(ext_dir, main)):
        raise _fail(f"manifest main entry point {main!r} is missing "
                    f"from disk — refusing to package")
    # "./extension.js" and "extension.js" package identically; the
    # archive member name must be normalized to match.
    main_rel = os.path.normpath(main).replace(os.sep, "/")
    files = _collect_files(ext_dir)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr(_zinfo("[Content_Types].xml"), _CONTENT_TYPES)
        zf.writestr(_zinfo("extension.vsixmanifest"),
                    _vsixmanifest(manifest))
        for rel in files:
            with open(os.path.join(ext_dir, rel), "rb") as fh:
                zf.writestr(_zinfo(f"extension/{rel}"), fh.read())
    return _verify(out_path, main_rel, manifest)


def _verify(out_path: str, main: str, manifest: dict) -> dict:
    """Reopen the archive and require the essentials before declaring
    success."""
    try:
        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()
            bad = zf.testzip()
            manifest_bytes = zf.read("extension.vsixmanifest")
        if bad is not None:
            raise _fail(f"written archive has a corrupt member: {bad}")
    except zipfile.BadZipFile as exc:
        raise _fail(f"written archive is not a valid zip: {exc}") from exc
    for required in ("extension.vsixmanifest", "[Content_Types].xml",
                     "extension/package.json", f"extension/{main}"):
        if required not in names:
            raise _fail(f"written archive lacks required member "
                        f"{required!r}")
    try:
        root = ET.fromstring(manifest_bytes)
    except ET.ParseError as exc:
        raise _fail(f"vsixmanifest is not parseable XML: {exc}") from exc
    # ElementTree reports namespaced tags as '{ns}local'; the VSIX
    # manifest namespace is part of the format, so compare local names.
    if root.tag.rsplit("}", 1)[-1] != "PackageManifest":
        raise _fail(f"vsixmanifest root element is {root.tag!r}, "
                    f"not PackageManifest")
    with open(out_path, "rb") as fh:
        blob = fh.read()
    return {
        "path": out_path,
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "files": len(names) - 2,  # minus the two structure members
        "version": manifest["version"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extension-dir", default=_DEFAULT_EXT_DIR)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    ext_dir = os.path.abspath(args.extension_dir)
    try:
        manifest = _load_manifest(ext_dir)
    except BuildError as exc:
        print(f"VSIX BUILD FAILED: {exc}", file=sys.stderr)
        return 1
    out_path = args.out or os.path.join(
        ext_dir, f"vscode-nyrqis-{manifest['version']}.vsix")
    try:
        report = build_vsix(ext_dir, out_path)
    except BuildError as exc:
        if os.path.exists(out_path):
            os.unlink(out_path)
        print(f"VSIX BUILD FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — fail-closed on anything
        if os.path.exists(out_path):
            os.unlink(out_path)
        print(f"VSIX BUILD FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"VSIX OK: {report['path']}")
    print(f"  version: {report['version']}  files: {report['files']}  "
          f"size: {report['size']} bytes")
    print(f"  sha256: {report['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
