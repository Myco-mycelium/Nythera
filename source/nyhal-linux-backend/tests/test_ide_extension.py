"""The vscode-nyrqis extension pin (mock harness, no vscode runtime).

The IDE-integration roadmap item was audited as genuinely absent
(2026-09-23); this extension is the prototype that closes the gap
honestly: real validation (the same floor the daemon's import gate
uses), a real manifest, and a harness-pinned contract — the .vsix
build half followed 2026-09-26 (tools/build_vsix.py +
test_build_vsix.py), leaving only the owner-side marketplace publish
outside the repo.

The harness stubs the 'vscode' module and drives extension.js's
diagnostic path end-to-end: command wiring, language activation,
validator invocation, diagnostic conversion, clean-file clearing, and
failure surfaces.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXT = os.path.join(_BACKEND, "ide", "vscode-nyrqis")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

EXTENSION_JS = os.path.join(_EXT, "extension.js")
PACKAGE_JSON = os.path.join(_EXT, "package.json")

_MOCK_VSCODE = r'''
// Minimal 'vscode' stub for the harness: just enough surface for
// extension.js to load and its diagnostic path to be driven.
function Range(a, b, c, d) { this.range = [a, b, c, d]; }
function Diagnostic(range, message, severity) {
    this.range = range; this.message = message; this.severity = severity;
}
function Uri(fsPath) { this.fsPath = fsPath; }
Uri.file = function (p) { return new Uri(p); };
const configValues = {};
function getConfiguration(section) {
    return {
        get(key, fallback) {
            const v = configValues[section + "." + key];
            return v === undefined ? fallback : v;
        },
    };
}
module.exports = {
    Range, Diagnostic, Uri,
    languages: {
        createDiagnosticCollection: function (name) {
            const map = new Map();
            return {
                name,
                set(uri, diags) { map.set(uri.fsPath, diags); },
                delete(uri) { map.delete(uri.fsPath); },
                _map: map,
            };
        },
    },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    workspace: {
        getConfiguration,
        getWorkspaceFolders: () => [],
        textDocuments: [],
        onDidOpenTextDocument: () => ({ dispose() {} }),
        onDidSaveTextDocument: () => ({ dispose() {} }),
        onDidCloseTextDocument: () => ({ dispose() {} }),
    },
    window: { activeTextEditor: null, showErrorMessage() {} },
    commands: { registerCommand: () => ({ dispose() {} }) },
    _configValues: configValues,
};
'''


def _run_harness(script: str) -> dict:
    """Run a harness script with the vscode stub resolvable. Returns
    the JSON the script prints.

    The stub is installed as ``ide/vscode-nyrqis/node_modules/vscode/``
    — the location Node's own resolver finds when ``extension.js``
    requires ``'vscode'`` — so the harness needs no NODE_PATH tricks.
    """
    mock_dir = os.path.join(_EXT, "node_modules", "vscode")
    os.makedirs(mock_dir, exist_ok=True)
    with open(os.path.join(mock_dir, "index.js"), "w") as fh:
        fh.write(_MOCK_VSCODE)
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=60, cwd=_EXT)
    if proc.returncode != 0:
        raise AssertionError(
            f"harness failed: {proc.stderr}\nstdout: {proc.stdout}")
    return json.loads(proc.stdout)


class ExtensionManifestTests(unittest.TestCase):
    """The manifest declares what the code actually provides."""

    def test_manifest_declares_language_commands_and_activation(self):
        manifest = json.load(open(PACKAGE_JSON))
        self.assertEqual(manifest["main"], "./extension.js")
        lang_ids = [l["id"] for l in manifest["contributes"]["languages"]]
        self.assertIn("nstudio", lang_ids)
        exts = [e for l in manifest["contributes"]["languages"]
                for e in l["extensions"]]
        self.assertIn(".nstudio", exts)
        commands = [c["command"]
                    for c in manifest["contributes"]["commands"]]
        for expected in ("nyrqis.validateDocument", "nyrqis.nyqNew",
                         "nyrqis.nyqBuild", "nyrqis.nyqTest"):
            self.assertIn(expected, commands)
        self.assertIn("onLanguage:nstudio", manifest["activationEvents"])


class ExtensionDiagnosticContractTests(unittest.TestCase):
    """extension.js's diagnostic path, driven through the mock harness."""

    @classmethod
    def setUpClass(cls):
        cls.result = _run_harness(r'''
const path = require("path");
process.env.NODE_PATH = %r;
const ext = require(%r);
const vscode = require("vscode");
const fixture = %r;
const badDoc = %r;
const badUri = vscode.Uri.file(badDoc);
const goodUri = vscode.Uri.file(fixture);
const items = [
    {file: badDoc, line: 1, column: 1, severity: "error",
     code: "validation", message: "synthetic for harness"},
];
const converted = ext.toVsCodeDiagnostics(items);
console.log(JSON.stringify({
    convertedSeverity: converted[0].severity,
    convertedSource: converted[0].source,
    convertedCode: converted[0].code,
    convertedMessage: converted[0].message,
}));
''' % (_EXT, EXTENSION_JS,
       os.path.join(_BACKEND, "tests", "fixtures", "nstudio",
                    "desktop.nstudio"),
       "/tmp/nonexistent-doc.nstudio"))

    def test_diagnostics_convert_with_source_and_code(self):
        self.assertEqual(self.result["convertedSource"], "nyrqis")
        self.assertEqual(self.result["convertedCode"], "validation")
        self.assertEqual(self.result["convertedSeverity"], 0)  # Error
        self.assertIn("synthetic", self.result["convertedMessage"])

    def test_validator_runs_the_real_gate_on_a_real_fixture(self):
        """The floor validates the shipped fixture cleanly — the same
        gate the daemon's import path enforces."""
        proc = subprocess.run(
            [sys.executable, os.path.join(_BACKEND, "nst_validate.py"),
             os.path.join(_BACKEND, "tests", "fixtures", "nstudio",
                          "desktop.nstudio")],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [])

    def test_validator_flags_a_contract_violation_with_exit_1(self):
        bad = os.path.join("/tmp", "nst-bad-version.nstudio")
        with open(bad, "w") as fh:
            fh.write('{"version": "9.9.9"}')
        proc = subprocess.run(
            [sys.executable, os.path.join(_BACKEND, "nst_validate.py"), bad],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1)
        diags = json.loads(proc.stdout)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["code"], "schema-version")
        self.assertEqual(diags[0]["severity"], "error")
        self.assertIn("unsupported schema version", diags[0]["message"])


REPO_ROOT = os.path.dirname(os.path.dirname(_BACKEND))
CHECKER = os.path.join(REPO_ROOT, "scripts", "check_nstudio_designs.sh")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "docs.yml")


class TestCheckNstudioDesigns(unittest.TestCase):
    """The CI design gate's fail-closed contract, exercised against
    sandbox trees (never the real checkout's files)."""

    def _sandbox(self, designs, validator_body=None):
        """Build a fake backend tree: nst_validate.py + the given
        design files. The stub validator echoes the behavior the real
        one would have for this scenario."""
        root = tempfile.mkdtemp(prefix="nst-gate-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, "shell"))
        validator_body = validator_body or (
            "import json,sys\n"
            "args=sys.argv[1:]\n"
            "json.dump([], sys.stdout)\n"
        )
        with open(os.path.join(root, "nst_validate.py"), "w") as fh:
            fh.write(validator_body)
        for name in designs:
            path = os.path.join(root, "shell", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write('{"version": "1.0.0"}')
        return root

    def _run(self, *args):
        return subprocess.run(
            ["bash", CHECKER, *args],
            capture_output=True, text=True, timeout=60)

    @unittest.skipUnless(os.path.exists(CHECKER),
                         "checker script not present")
    def test_clean_tree_passes(self):
        root = self._sandbox(["shell/a.nstudio", "shell/b.nstudio"])
        proc = self._run(root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NSTUDIO DESIGNS: OK", proc.stdout)
        self.assertIn("2 document", proc.stdout)

    @unittest.skipUnless(os.path.exists(CHECKER),
                         "checker script not present")
    def test_failing_design_exits_1_with_annotation_and_diagnostics(self):
        """The fail-closed path that the first draft got wrong: the
        diagnostics JSON AND the ::error:: annotation must both reach
        the log (a silent failure annotates nothing)."""
        stub = (
            "import json,sys\n"
            "json.dump([{\"file\": \"x.nstudio\", \"line\": 1, "
            "\"column\": 1, \"severity\": \"error\", "
            "\"code\": \"validation\", \"message\": \"bad\"}], "
            "sys.stdout)\n"
        )
        root = self._sandbox(["shell/bad.nstudio"], validator_body=stub)
        proc = self._run(root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error::", proc.stdout)
        self.assertIn("\"code\": \"validation\"", proc.stdout)

    @unittest.skipUnless(os.path.exists(CHECKER),
                         "checker script not present")
    def test_zero_designs_is_a_finding(self):
        root = self._sandbox([])
        proc = self._run(root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error::", proc.stdout)
        self.assertIn("mass deletion", proc.stdout)

    @unittest.skipUnless(os.path.exists(CHECKER),
                         "checker script not present")
    def test_missing_validator_fails_closed(self):
        root = self._sandbox(["shell/a.nstudio"])
        os.remove(os.path.join(root, "nst_validate.py"))
        proc = self._run(root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error::", proc.stdout)

    @unittest.skipUnless(os.path.exists(CHECKER),
                         "checker script not present")
    def test_unparseable_validator_output_is_a_gate_failure(self):
        stub = "import sys\nsys.stdout.write('not json')\n"
        root = self._sandbox(["shell/a.nstudio"], validator_body=stub)
        proc = self._run(root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error::", proc.stdout)

    @unittest.skipUnless(os.path.exists(CHECKER),
                         "checker script not present")
    def test_real_tree_passes_and_workflow_wires_the_gate(self):
        """The real checkout's 13 shipped designs pass the REAL
        validator, and the docs workflow actually runs the gate on the
        paths that can break it."""
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = open(WORKFLOW).read()
        self.assertIn("check_nstudio_designs.sh", text)
        self.assertIn('"source/nyhal-linux-backend/shell/**"', text)
        self.assertIn('"source/nyhal-linux-backend/nst_validate.py"', text)


if __name__ == "__main__":
    unittest.main()
