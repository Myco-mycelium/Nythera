# Nyrqis IPC Operations

Complete reference for all IPC operations exposed by the Nyrqis backend.

## Overview

The Nyrqis backend communicates over a Unix datagram transport with
SCM_CREDENTIALS authentication. All operations use JSON request/response
over CALL/REPLY datagrams.

**Transport:** Unix domain socket (`/run/nyrqis/<service>.sock`)
**Authentication:** Kernel-attached SCM_CREDENTIALS (pid, uid, gid)
**Max payload:** 48 KiB per datagram

## NUI Service

**Service name:** `nyrqis.backend.nui`
**Socket:** `/run/nyrqis/nui.sock`

### nui_validate

Validate a `.nstudio` document against the NUI contract.

**Request:**
```json
{
  "service": "nui",
  "op": "nui_validate",
  "document": "<.nstudio JSON string>"
}
```

**Response (success):**
```json
{
  "ok": true,
  "service": "nyrqis.backend.nui",
  "service_version": "1.0",
  "summary": {
    "version": "1.0.0",
    "engine": "rust",
    "screens": ["desktop", "lock"],
    "components": 37,
    "behaviors": 11,
    "bindings": 6
  }
}
```

**Response (failure):**
```json
{
  "ok": false,
  "error": "nui_validate failed: <validation message>"
}
```

**Errors:**
- `forbidden` — sender is not the operator
- `document must be a JSON string` — missing or invalid document
- `document exceeds the 49152-byte budget` — document too large

### nui_load

Validate and persist a `.nstudio` document as the daemon's shell design.

**Request:**
```json
{
  "service": "nui",
  "op": "nui_load",
  "document": "<.nstudio JSON string>"
}
```

**Response (success):**
```json
{
  "ok": true,
  "service": "nyrqis.backend.nui",
  "service_version": "1.0",
  "summary": { ... },
  "path": "/var/lib/nyrqis/ui/shell.nstudio"
}
```

**Response (failure):**
```json
{
  "ok": false,
  "error": "nui_load failed: <validation message>"
}
```

**Notes:**
- Atomic write (write to temp, then `os.replace`)
- Requires daemon state directory (`--state-file`)
- Persisted design is at `<state_dir>/ui/shell.nstudio`

### nui_current

Report the daemon's loaded shell design.

**Request:**
```json
{
  "service": "nui",
  "op": "nui_current"
}
```

**Response (no design loaded):**
```json
{
  "ok": true,
  "loaded": false,
  "service": "nyrqis.backend.nui",
  "service_version": "1.0"
}
```

**Response (design loaded):**
```json
{
  "ok": true,
  "loaded": true,
  "valid": true,
  "path": "/var/lib/nyrqis/ui/shell.nstudio",
  "service": "nyrqis.backend.nui",
  "service_version": "1.0",
  "summary": { ... }
}
```

**Response (stale design):**
```json
{
  "ok": true,
  "loaded": true,
  "valid": false,
  "path": "/var/lib/nyrqis/ui/shell.nstudio",
  "error": "persisted design no longer validates: <message>"
}
```

### shell_run

Run the loaded shell design: exercise all behaviors, apply bindings,
return runtime state.

**Request:**
```json
{
  "service": "nui",
  "op": "shell_run",
  "document": "<optional .nstudio JSON>"
}
```

If `document` is omitted, uses the persisted design.

**Response:**
```json
{
  "ok": true,
  "summary": {
    "version": "1.0.0",
    "screens": ["desktop", "lock"],
    "components": 37,
    "behaviors": 11,
    "bindings": 6
  },
  "behaviors_executed": 11,
  "final_states": {
    "theme": "Eclipse",
    "volume": "60",
    "clockTime": "14:32"
  },
  "text_preview": "screen desktop 1440x900\n  Window window_desktop (0,0 1440x900)\n    ...",
  "log": ["Applied binding: theme -> themeName", ...]
}
```

### shell_render

Render the loaded shell design to PNG images.

**Request:**
```json
{
  "service": "nui",
  "op": "shell_render",
  "document": "<optional .nstudio JSON>",
  "theme": "Eclipse",
  "scale": 1.0,
  "screens": ["desktop"]
}
```

**Response:**
```json
{
  "ok": true,
  "service": "nyrqis.backend.nui",
  "service_version": "1.0",
  "screens": {
    "desktop": "<base64-encoded PNG>",
    "lock": "<base64-encoded PNG>"
  },
  "summary": {
    "version": "1.0.0",
    "screens": ["desktop", "lock"],
    "theme": "Eclipse",
    "scale": 1.0
  }
}
```

**Notes:**
- Requires PIL/Pillow
- `screens` parameter filters which screens to render (optional)
- Returns base64-encoded PNG images

### shell_display

Display the loaded shell design in a live SDL2 window (or headless PNG).

**Request:**
```json
{
  "service": "nui",
  "op": "shell_display",
  "document": "<optional .nstudio JSON>",
  "theme": "Eclipse",
  "scale": 1.0
}
```

**Response (windowed):**
```json
{
  "ok": true,
  "service": "nyrqis.backend.nui",
  "service_version": "1.0",
  "displayed": true,
  "screens": ["desktop", "lock"]
}
```

**Response (headless fallback):**
```json
{
  "ok": true,
  "service": "nyrqis.backend.nui",
  "service_version": "1.0",
  "displayed": false,
  "reason": "no DISPLAY — headless fallback",
  "screens": {
    "desktop": "<base64-encoded PNG>"
  }
}
```

**Notes:**
- Requires pysdl2
- When `DISPLAY` is set: opens a live SDL2 window
- When headless: falls back to PNG export (same as `shell_render`)

## Control Service

**Service name:** `nyrqis.backend.control`
**Socket:** `/run/nyrqis/control.sock`

### ping

Health check endpoint.

**Request:**
```json
{
  "service": "control",
  "op": "ping"
}
```

**Response:**
```json
{
  "ok": true,
  "service": "nyrqis.backend.control",
  "service_version": "1.0",
  "status": "running"
}
```

### status

System status (requires `CAP_SYSTEM_INFO`).

**Request:**
```json
{
  "service": "control",
  "op": "status"
}
```

**Response:**
```json
{
  "ok": true,
  "uptime": 3600,
  "containers": 2,
  "memory_mb": 256,
  "cpu_percent": 12.5
}
```

## Error Responses

All operations return errors in this format:

```json
{
  "ok": false,
  "error": "<error message>"
}
```

Common error messages:
- `forbidden: the NUI service is operator-only` — non-operator sender
- `unknown operation: "<op>"` — invalid operation name
- `internal error` — unexpected server error
- `bad request: expected a JSON object` — malformed request

## Using with nyrqisctl

The `nyrqisctl` CLI wraps these operations:

```bash
# Validate a design
nyrqisctl nui validate desktop.nstudio

# Load a design
nyrqisctl nui load desktop.nstudio

# Check loaded design
nyrqisctl nui current

# Run the shell
nyrqisctl nui run

# Render to PNG
nyrqisctl nui render --theme Eclipse --output ./render/

# Display in window
nyrqisctl nui display
```
