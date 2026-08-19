#!/usr/bin/env python3
"""Preview server for NUI shell designs.

Serves rendered .nstudio files as PNG images over HTTP. Useful for
development and demonstration of the Nyrqis shell.

Usage:
    python3 tools/preview_server.py [PORT] [--file FILE.nstudio]
                                     [--theme Eclipse|Solar] [--scale 1.0]

Endpoints:
    GET /                    — HTML page with screen selector
    GET /render/<screen_id>  — PNG image of the specified screen
    GET /render               — PNG of the first screen
    GET /api/info             — JSON with document metadata
    GET /api/screens          — JSON list of screens
    GET /api/state            — JSON runtime state (via shell.run())

Default: serves desktop.nstudio on port 8080.

References:
- NUI-SCHEMA §3: layout system
- ADR-0025: NUI runtime consumption
"""

import argparse
import io
import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# Ensure paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "nyhal-linux-backend"))

from ui.nstudio import load as nstudio_load, NstudioDocument
from ui.compositor import Compositor

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False


# Global state
_document: Optional[NstudioDocument] = None
_compositor: Optional[Compositor] = None
_file_path: str = ""
_file_mtime: float = 0.0  # last modification time
_reload_counter: int = 0  # increments on each file change


class _FileChangeHandler(FileSystemEventHandler):
    """Watches the .nstudio file for changes and reloads."""

    def on_modified(self, event):
        global _document, _file_mtime, _reload_counter
        if event.src_path == os.path.abspath(_file_path):
            try:
                _document = nstudio_load(_file_path)
                _file_mtime = os.path.getmtime(_file_path)
                _reload_counter += 1
                print(f"  [reload] {_reload_counter}: reloaded {_document.component_ids().__len__()} components")
            except Exception as e:
                print(f"  [reload] ERROR: {e}")


class PreviewHandler(BaseHTTPRequestHandler):
    """HTTP handler for the NUI preview server."""

    def do_GET(self):
        global _document, _compositor, _file_path

        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
        elif self.path.startswith("/render/"):
            screen_id = self.path[len("/render/"):]
            self._serve_render(screen_id)
        elif self.path == "/render":
            self._serve_render(None)
        elif self.path == "/api/info":
            self._serve_api_info()
        elif self.path == "/api/screens":
            self._serve_api_screens()
        elif self.path == "/api/state":
            self._serve_api_state()
        elif self.path == "/api/changes":
            self._serve_api_changes()
        else:
            self.send_error(404)

    def _serve_index(self):
        """Serve the HTML index page."""
        screens = _document.screens if _document else []
        screen_options = "\n".join(
            f'<option value="{s.id}">{s.id} ({s.size.get("width", "?")}x{s.size.get("height", "?")})</option>'
            for s in screens
        )
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Nyrqis Shell Preview</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               background: #1a1a1a; color: #e6e6e6; margin: 0; padding: 20px; }}
        h1 {{ color: #6495ed; }}
        .controls {{ margin: 16px 0; }}
        select, button {{ padding: 8px 12px; border: 1px solid #555;
                         background: #333; color: #e6e6e6; border-radius: 4px;
                         font-size: 14px; }}
        select:hover, button:hover {{ background: #444; }}
        button {{ background: #6495ed; border: none; cursor: pointer; }}
        button:hover {{ background: #7ab5ff; }}
        .preview {{ margin: 20px 0; border: 1px solid #555; display: inline-block; }}
        .preview img {{ display: block; max-width: 100%; }}
        .info {{ margin: 16px 0; padding: 12px; background: #2a2a2a; border-radius: 4px; }}
        .info pre {{ margin: 0; font-size: 13px; color: #aaa; }}
    </style>
</head>
<body>
    <h1>Nyrqis Shell Preview</h1>
    <p>File: <code>{os.path.basename(_file_path)}</code> | Theme: {_compositor.theme_name} | Scale: {_compositor.scale}x</p>

    <div class="controls">
        <label>Screen:</label>
        <select id="screen-select">
            {screen_options}
        </select>
        <button onclick="refresh()">Refresh</button>
        <button onclick="toggleTheme()">Toggle Theme</button>
    </div>

    <div class="preview">
        <img id="preview-img" src="/render" alt="Shell Preview" />
    </div>

    <div class="info">
        <pre id="info-text">Loading...</pre>
    </div>

    <script>
        const select = document.getElementById('screen-select');
        const img = document.getElementById('preview-img');
        const info = document.getElementById('info-text');

        function refresh() {{
            const screen = select.value;
            img.src = '/render/' + screen + '?t=' + Date.now();
            fetch('/api/info')
                .then(r => r.json())
                .then(d => {{ info.textContent = JSON.stringify(d, null, 2); }});
        }}

        function toggleTheme() {{
            fetch('/api/state')
                .then(r => r.json())
                .then(d => {{
                    info.textContent = JSON.stringify(d, null, 2);
                }});
        }}

        select.addEventListener('change', refresh);

        // Load info
        fetch('/api/info')
            .then(r => r.json())
            .then(d => {{ info.textContent = JSON.stringify(d, null, 2); }});

        // Auto-refresh: poll /api/changes every 2 seconds
        let lastCounter = 0;
        setInterval(() => {{
            fetch('/api/changes')
                .then(r => r.json())
                .then(d => {{
                    if (d.counter > lastCounter && lastCounter > 0) {{
                        console.log('File changed, refreshing...');
                        refresh();
                    }}
                    lastCounter = d.counter;
                }});
        }}, 2000);
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_render(self, screen_id: Optional[str]):
        """Serve a rendered screen as PNG."""
        global _document, _compositor
        try:
            img = _compositor.render_screen(_document, screen_id=screen_id)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_api_info(self):
        """Serve document metadata as JSON."""
        global _document, _compositor
        info = {
            "file": os.path.basename(_file_path),
            "version": _document.version if _document else "unknown",
            "theme": _compositor.theme_name if _compositor else "unknown",
            "scale": _compositor.scale if _compositor else 1.0,
            "screens": len(_document.screens) if _document else 0,
            "components": len(_document.component_ids()) if _document else 0,
            "behaviors": len(_document.behaviors) if _document else 0,
            "bindings": len(_document.bindings) if _document else 0,
        }
        self._serve_json(info)

    def _serve_api_screens(self):
        """Serve screen list as JSON."""
        global _document
        screens = []
        for s in (_document.screens if _document else []):
            screens.append({
                "id": s.id,
                "width": s.size.get("width", 0),
                "height": s.size.get("height", 0),
            })
        self._serve_json(screens)

    def _serve_api_state(self):
        """Serve runtime state as JSON."""
        global _document
        try:
            from ui.shell import NyrqisShell
            shell = NyrqisShell(_document)
            result = shell.run()
            self._serve_json(result)
        except Exception as e:
            self._serve_json({"error": str(e)})

    def _serve_api_changes(self):
        """Poll for file changes. Returns counter that increments on reload."""
        global _reload_counter
        self._serve_json({"counter": _reload_counter})

    def _serve_json(self, data):
        """Serve JSON response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def main():
    global _document, _compositor, _file_path

    parser = argparse.ArgumentParser(description="NUI Preview Server")
    parser.add_argument("port", nargs="?", type=int, default=8080,
                        help="Port to listen on (default: 8080)")
    parser.add_argument("--file", "-f", default=None,
                        help=".nstudio file to serve")
    parser.add_argument("--theme", default="Eclipse",
                        choices=["Eclipse", "Solar"],
                        help="Theme (default: Eclipse)")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Scale factor (default: 1.0)")
    args = parser.parse_args()

    # Find the file
    if args.file:
        _file_path = args.file
    else:
        # Try to find desktop.nstudio
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "source",
                         "nyhal-linux-backend", "tests", "fixtures",
                         "nstudio", "desktop.nstudio"),
            os.path.join(os.path.dirname(__file__), "..", "tests",
                         "fixtures", "nstudio", "desktop.nstudio"),
        ]
        for c in candidates:
            if os.path.exists(c):
                _file_path = os.path.abspath(c)
                break
        if not _file_path:
            print("ERROR: No .nstudio file found. Use --file to specify one.",
                  file=sys.stderr)
            return 1

    # Load the document
    print(f"Loading: {_file_path}")
    _document = nstudio_load(_file_path)
    _compositor = Compositor(theme_name=args.theme, scale=args.scale)

    print(f"Screens: {len(_document.screens)}")
    print(f"Components: {len(_document.component_ids())}")
    global _file_mtime
    _file_mtime = os.path.getmtime(_file_path)

    print(f"Theme: {args.theme} | Scale: {args.scale}x")
    print(f"\nPreview server running at http://localhost:{args.port}")
    print(f"  /              — HTML preview page")
    print(f"  /render/<id>   — PNG image of screen")
    print(f"  /api/info      — JSON metadata")
    print(f"  /api/screens   — JSON screen list")
    print(f"  /api/state     — JSON runtime state")
    print(f"  /api/changes   — Poll for file changes")

    # Start file watcher
    observer = None
    if HAS_WATCHDOG:
        observer = Observer()
        handler = _FileChangeHandler()
        watch_dir = os.path.dirname(os.path.abspath(_file_path))
        observer.schedule(handler, watch_dir, recursive=False)
        observer.start()
        print(f"  [watch] Watching {watch_dir} for changes")
    else:
        print(f"  [watch] watchdog not installed — auto-reload disabled")

    print(f"\nPress Ctrl+C to stop.\n")

    server = HTTPServer(("0.0.0.0", args.port), PreviewHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
    if observer:
        observer.stop()
        observer.join()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
