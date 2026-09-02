# Nyrqis Shell Defaults

This directory contains the default shell designs that ship with Nyrqis.
When a user runs `nyrqis_init` without a custom design, these are the
fallbacks that provide a working desktop out of the box.

## Files

| File | Description |
|------|-------------|
| `default-shell.nstudio` | Minimal desktop shell — taskbar, clock, start menu, search. Lightweight, good for low-spec machines. |
| `desktop.nstudio` | Full desktop shell — 30 components, 8 behaviors, 6 bindings, multi-screen, Dock, AppGrid, Launcher. The complete Nyrqis experience. |

## How It Works

`nyrqis_init.py` searches for a shell design in this order:

1. `--design /path/to/file.nstudio` (explicit)
2. `~/.nyrqis/shell.nstudio` (user's choice)
3. `shell/defaults/default-shell.nstudio` (minimal default)
4. `shell/defaults/desktop.nstudio` (full default)
5. `tests/fixtures/nstudio/desktop.nstudio` (test fixture)
6. `tests/fixtures/nstudio/nyrqis-shell.nstudio` (test fixture)

The first file found is used.

## Design Format

Shell designs use the NUI `.nstudio` format (JSON). Key sections:

```jsonc
{
  "version": "1.0.0",           // Schema version (must be "1.0.0")
  "project": { ... },           // Project metadata
  "themes": { "active": "Eclipse" },  // Theme selection
  "components": [],             // Reusable component masters
  "screens": [ {                // Screen definitions
    "id": "main_screen",
    "size": { "width": 1920, "height": 1080 },
    "root": { ... }             // Component tree
  }],
  "behaviors": [],              // WHEN/IF/DO event handlers
  "bindings": [],               // State → property sync
  "states": {},                 // State variables
  "stateScopes": {},            // Persistent/session/global scopes
  "animations": [],             // Keyframe animations
  "locales": {},                // Localization tables
  "resources": { "assets": [] } // Asset catalog
}
```

## Component Types

The NUI registry includes 66 component types across five categories:

- **Shell**: Taskbar, StartMenu, WindowFrame, CommandPalette, LockScreen, Dock, AppGrid, Clock, TitleBar, ...
- **Data**: List, DataTable, TreeView, Menu
- **Form**: DatePicker, FilePicker, SettingsPanel
- **Media**: Video, Audio, MediaPlayer
- **Developer**: Terminal, CodeEditor, LogViewer

See `ui/contracts/nui-api-v1.json` for the full registry.

## Creating Custom Shells

1. Start from `default-shell.nstudio` and add components
2. Validate with: `python3 nyrqis_run.py your-shell.nstudio --validate-only`
3. Render a preview: `python3 nyrqis_run.py your-shell.nstudio -o preview.png`
4. Load into the daemon: `nyrqisctl nui load your-shell.nstudio`
5. Set as default: copy to `~/.nyrqis/shell.nstudio`

## References

- NFS-001: NUI Schema Specification
- NFS-006: Component Vocabulary
- ADR-0025: NUI Runtime Consumption
- doc #14: Nyrqis Desktop Shell as a running product
