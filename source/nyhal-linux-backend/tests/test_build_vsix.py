"""The .vsix builder pin (sandbox trees, never the real checkout).

The IDE-integration item's packaging half is closed honestly: the
builder assembles a real, installable VSIX structure with the standard
library alone — no vsce, no npm registry, no network. These tests pin
the fail-closed contract and the determinism guarantee against sandbox
extension trees; the real tree is only ever *counted*, never written.

Structure expectations come from the VSIX package format: the
``extension/`` directory plus the two root members
(``extension.vsixmanifest`` §4, ``[Content_Types].xml`` §5).
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(_BACKEND, "tools", "build_vsix.py")
_REAL_EXT = os.path.join(_BACKEND, "ide", "vscode-nyrqis")

_MANIFEST = {
    "name": "vscode-nyrqis",
    "displayName": "Nyrqis NUI Design",
    "description": "Editing support for Nyrqis .nstudio documents.",
    "version": "0.1.0",
    "publisher": "nyrqis",
    "engines": {"vscode": "^1.80.0"},
    "main": "./extension.js",
}


class _SandboxExt:
    """A minimal but complete extension tree on disk."""

    def __init__(self, root):
        self.root = root
        self.ext_dir = os.path.join(root, "ide", "vscode-nyrqis")
        os.makedirs(self.ext_dir)
        self.write("package.json", json.dumps(_MANIFEST))
        self.write("extension.js", "const vscode = require('vscode');\n"
                   "module.exports = { activate() {} };\n")
        self.write("language-configuration.json", "{}")

    def write(self, rel, content):
        path = os.path.join(self.ext_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path


class _RunIn:
    """Run the real builder against a sandbox tree."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="vsix-sandbox-")
        self.ext = _SandboxExt(self.root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run(self, *args):
        return subprocess.run(
            [sys.executable, BUILDER,
             "--extension-dir", self.ext.ext_dir, *args],
            capture_output=True, text=True, timeout=60)


class TestVsixBuilder(unittest.TestCase):

    def setUp(self):
        self.sandbox = _RunIn()
        self.addCleanup(self.sandbox.cleanup)

    def _out(self, name="out.vsix"):
        return os.path.join(self.sandbox.root, name)

    # -- success path -------------------------------------------------

    def test_real_extension_builds_with_expected_members(self):
        """The real tree packages: both structure members, the
        manifest, the entry point, and no node_modules stub."""
        out = self._out("real.vsix")
        proc = self.sandbox.run("--out", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        self.assertIn("extension.vsixmanifest", names)
        self.assertIn("[Content_Types].xml", names)
        self.assertIn("extension/package.json", names)
        self.assertIn("extension/extension.js", names)
        self.assertIn("extension/language-configuration.json", names)

    def test_output_reports_size_sha_and_file_count(self):
        out = self._out()
        proc = self.sandbox.run("--out", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("sha256:", proc.stdout)
        size = os.path.getsize(out)
        self.assertIn(f"size: {size} bytes", proc.stdout)
        with open(out, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        self.assertIn(digest, proc.stdout)

    def test_vsixmanifest_identity_and_asset(self):
        import xml.etree.ElementTree as ET

        def local(elem, name):
            """First descendant whose namespace-stripped tag is name."""
            for e in elem.iter():
                if e.tag.rsplit("}", 1)[-1] == name:
                    return e
            return None

        out = self._out()
        self.sandbox.run("--out", out)
        with zipfile.ZipFile(out) as zf:
            root = ET.fromstring(zf.read("extension.vsixmanifest"))
        self.assertEqual(root.tag.rsplit("}", 1)[-1], "PackageManifest")
        identity = local(root, "Identity")
        self.assertEqual(identity.get("Id"), "nyrqis.vscode-nyrqis")
        self.assertEqual(identity.get("Version"), "0.1.0")
        asset = local(root, "Asset")
        self.assertEqual(asset.get("Path"), "extension/package.json")
        target = local(root, "InstallationTarget")
        self.assertEqual(target.get("Id"), "Microsoft.VisualStudio.Code")

    def test_rebuild_is_byte_identical(self):
        """Two runs over the same tree → same bytes (the rebuild can be
        hash-checked instead of trusted)."""
        a, b = self._out("a.vsix"), self._out("b.vsix")
        self.sandbox.run("--out", a)
        self.sandbox.run("--out", b)
        with open(a, "rb") as f1, open(b, "rb") as f2:
            self.assertEqual(hashlib.sha256(f1.read()).hexdigest(),
                             hashlib.sha256(f2.read()).hexdigest())

    def test_node_modules_stub_is_never_packaged(self):
        """The mock-harness vscode stub (node_modules/) never enters
        the archive — a leaked stub would ship a fake 'vscode' module
        inside the package."""
        stub = os.path.join(self.sandbox.ext.ext_dir, "node_modules",
                            "vscode")
        os.makedirs(stub, exist_ok=True)
        with open(os.path.join(stub, "index.js"), "w") as fh:
            fh.write("module.exports = {};\n")
        out = self._out()
        proc = self.sandbox.run("--out", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with zipfile.ZipFile(out) as zf:
            self.assertFalse(
                [n for n in zf.namelist()
                 if n.startswith("extension/node_modules")])

    def test_content_types_declares_all_packaged_extensions(self):
        out = self._out()
        self.sandbox.run("--out", out)
        with zipfile.ZipFile(out) as zf:
            ct = zf.read("[Content_Types].xml").decode("utf-8")
        for ext in ("vsixmanifest", "json", "js"):
            self.assertIn(f'Extension="{ext}"', ct)

    # -- fail-closed refusals -----------------------------------------

    def _break_manifest(self, mutate):
        manifest = dict(_MANIFEST)
        mutate(manifest)
        self.sandbox.ext.write("package.json", json.dumps(manifest))

    def test_missing_required_field_refuses(self):
        self._break_manifest(lambda m: m.pop("publisher"))
        proc = self.sandbox.run("--out", self._out())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("missing required fields", proc.stderr)
        self.assertFalse(os.path.exists(self._out()))

    def test_missing_engines_refuses(self):
        self._break_manifest(lambda m: m.pop("engines"))
        proc = self.sandbox.run("--out", self._out())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("engines.vscode", proc.stderr)

    def test_missing_entry_point_refuses(self):
        os.unlink(os.path.join(self.sandbox.ext.ext_dir, "extension.js"))
        proc = self.sandbox.run("--out", self._out())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("refusing to package", proc.stderr)
        self.assertFalse(os.path.exists(self._out()))

    def test_missing_package_json_refuses(self):
        os.unlink(os.path.join(self.sandbox.ext.ext_dir, "package.json"))
        proc = self.sandbox.run("--out", self._out())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no package.json", proc.stderr)

    def test_empty_tree_refuses(self):
        """A directory without even a package.json is refused with the
        manifest error (the manifest gate runs before the file walk)."""
        empty = os.path.join(self.sandbox.root, "empty-ext")
        os.makedirs(empty)
        proc = subprocess.run(
            [sys.executable, BUILDER, "--extension-dir", empty,
             "--out", self._out()],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no package.json", proc.stderr)
        self.assertFalse(os.path.exists(self._out()))

    def test_unreadable_package_json_refuses(self):
        self.sandbox.ext.write("package.json", "{not json")
        proc = self.sandbox.run("--out", self._out())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unreadable", proc.stderr)


if __name__ == "__main__":
    unittest.main()
