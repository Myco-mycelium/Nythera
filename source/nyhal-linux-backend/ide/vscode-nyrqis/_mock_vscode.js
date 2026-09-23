
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
