'use strict';
/**
 * vscode-nyrqis — editing support for .nstudio shell-design documents.
 *
 * Validation architecture (the honest part): the diagnostics come from
 * nst_validate.py, which runs the pure-Python import-gate floor
 * (ui/nstudio.loads) — the SAME contract validation the daemon's
 * operator-only nui_validate op enforces on import (ADR-0025). The
 * extension never talks to the daemon: the import gate is
 * operator-only by design, and an editor must not need operator
 * authority just to show squiggles.
 *
 * The daemon round-trip (`nyrqisctl nui validate`) remains available
 * from the terminal for the operator workflow; the extension's nyq
 * commands shell out to the SDK CLI for scaffold/build/test.
 */

const vscode = require('vscode');
const cp = require('child_process');
const path = require('path');
const fs = require('fs');

const DIAGNOSTIC_COLLECTION =
  vscode.languages.createDiagnosticCollection('nyrqis-nstudio');

/** Resolve the validator script: workspace-relative backendRoot first,
 * then a checkout rooted two directories above the extension's own
 * install location (the in-repo extension layout), else an error. */
function resolveValidator(pythonPath, config) {
  const backendRoot = config.get('backendRoot', '');
  if (backendRoot) {
    const candidate = path.join(backendRoot, 'nst_validate.py');
    if (fs.existsSync(candidate)) {
      return { script: candidate, cwd: backendRoot };
    }
    throw new Error(
      `nyrqis.backendRoot ("${backendRoot}") has no nst_validate.py`);
  }
  // In-repo layout: <backend>/ide/vscode-nyrqis/extension.js →
  // <backend>/nst_validate.py
  const candidate = path.resolve(__dirname, '..', '..', 'nst_validate.py');
  if (fs.existsSync(candidate)) {
    return { script: candidate, cwd: path.resolve(__dirname, '..', '..') };
  }
  throw new Error(
    'nst_validate.py not found — set nyrqis.backendRoot to the Nyrqis ' +
    'Linux Backend checkout');
}

/** Run nst_validate.py over the given files; resolves to the parsed
 * diagnostics array (rejects on a non-usage failure). */
function runValidator(uris) {
  const config = vscode.workspace.getConfiguration('nyrqis');
  const pythonPath = config.get('pythonPath', 'python3');
  let resolved;
  try {
    resolved = resolveValidator(pythonPath, config);
  } catch (err) {
    return Promise.reject(err);
  }
  const args = [pythonPath, resolved.script, ...uris.map((u) => u.fsPath)];
  return new Promise((resolve, reject) => {
    cp.execFile(args[0], args.slice(1), { cwd: resolved.cwd, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        // Exit 0 = valid; exit 1 = error diagnostics present (stdout
        // still carries the JSON); exit 2 = usage/internal failure.
        if (err && err.code !== 1) {
          return reject(new Error(
            `nst_validate exited ${err.code}: ${err.message}`));
        }
        try {
          resolve(JSON.parse(stdout));
        } catch (parseErr) {
          reject(new Error(`nst_validate produced non-JSON output: ${parseErr.message}`));
        }
      });
  });
}

/** Convert validator diagnostics into VS Code diagnostics. The
 * validator anchors document-level violations to (1,1) — the floor's
 * exceptions carry messages, not positions. */
function toVsCodeDiagnostics(items) {
  return items.map((item) => {
    const range = new vscode.Range(
      Math.max(0, (item.line || 1) - 1),
      Math.max(0, (item.column || 1) - 1),
      Math.max(0, (item.line || 1) - 1),
      Number.MAX_SAFE_INTEGER);
    const severity = item.severity === 'warning'
      ? vscode.DiagnosticSeverity.Warning
      : vscode.DiagnosticSeverity.Error;
    const diag = new vscode.Diagnostic(range, item.message, severity);
    diag.source = 'nyrqis';
    diag.code = item.code;
    return diag;
  });
}

function validateUris(uris) {
  if (!uris.length) {
    return Promise.resolve();
  }
  return runValidator(uris).then((items) => {
    const byFile = new Map();
    for (const item of items) {
      const key = item.file;
      if (!byFile.has(key)) {
        byFile.set(key, []);
      }
      byFile.get(key).push(item);
    }
    // Files with diagnostics get them set; validated-but-clean files
    // get an explicit clear (never a stale squiggle).
    for (const uri of uris) {
      const found = byFile.get(uri.fsPath) || [];
      DIAGNOSTIC_COLLECTION.set(uri, toVsCodeDiagnostics(found));
    }
  }).catch((err) => {
    vscode.window.showErrorMessage(`Nyrqis validation failed: ${err.message}`);
  });
}

function activate(context) {
  context.subscriptions.push(DIAGNOSTIC_COLLECTION);

  // Validate on open, save (configurable), and explicit command.
  const validateActive = () => {
    const editor = vscode.window.activeTextEditor;
    if (editor && editor.document.languageId === 'nstudio') {
      validateUris([editor.document.uri]);
    }
  };
  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument((doc) => {
      if (doc.languageId === 'nstudio') {
        validateUris([doc.uri]);
      }
    }));
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const config = vscode.workspace.getConfiguration('nyrqis');
      if (config.get('validateOnSave', true)
          && doc.languageId === 'nstudio') {
        validateUris([doc.uri]);
      }
    }));
  context.subscriptions.push(
    vscode.workspace.onDidCloseTextDocument((doc) => {
      DIAGNOSTIC_COLLECTION.delete(doc.uri);
    }));

  context.subscriptions.push(vscode.commands.registerCommand(
    'nyrqis.validateDocument', validateActive));

  // The nyq SDK commands: shell out to the project's own tooling the
  // way the operator would run it.
  const nyq = (subcommand) => () => {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders || !workspaceFolders.length) {
      vscode.window.showErrorMessage(
        'Nyrqis: open a workspace folder first');
      return;
    }
    const cwd = workspaceFolders[0].uri.fsPath;
    const terminal = vscode.window.createTerminal({
      name: `nyq ${subcommand}`,
      cwd,
    });
    terminal.show();
    terminal.sendText(`nyq ${subcommand}`);
  };
  context.subscriptions.push(
    vscode.commands.registerCommand('nyrqis.nyqNew', () => {
      const workspaceFolders = vscode.workspace.workspaceFolders;
      const cwd = workspaceFolders && workspaceFolders.length
        ? workspaceFolders[0].uri.fsPath : undefined;
      vscode.window.showInputBox({
        prompt: 'Project name for nyq new',
        placeHolder: 'my-nyrqis-app',
      }).then((name) => {
        if (!name) {
          return;
        }
        const terminal = vscode.window.createTerminal({ name: 'nyq new', cwd });
        terminal.show();
        terminal.sendText(`nyq new ${name}`);
      });
    }));
  context.subscriptions.push(
    vscode.commands.registerCommand('nyrqis.nyqBuild', nyq('build')));
  context.subscriptions.push(
    vscode.commands.registerCommand('nyrqis.nyqTest', nyq('test')));

  // Validate any .nstudio documents already open at activation.
  for (const doc of vscode.workspace.textDocuments) {
    if (doc.languageId === 'nstudio') {
      validateUris([doc.uri]);
    }
  }
}

function deactivate() {}

module.exports = { activate, deactivate, toVsCodeDiagnostics };
