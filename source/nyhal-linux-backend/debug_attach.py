#!/usr/bin/env python3
"""The D7 attach UX — client-side session orchestration over the
ALREADY-LANDED debug channel (DBG-001 v0.7.0's security surface).

What this module IS (and is not)
--------------------------------

The D7 decision (AG decision log, 2026-09-23, Option B) put the debugger
INSIDE the debugged container: a ``debug: true`` manifest class plus
``CAP-DEBUG-ATTACH`` (NPS-011 v1.4.0), with debugpy/gdbserver running in
the container and the seccomp ptrace denial relaxed for that class only
(``backend/seccomp.py`` ``_DEBUG_RELAXED_SYSCALLS``, construction-time).
Everything security-relevant ALREADY shipped (2026-09-23/25, pinned by
``tests/test_debug_manifest_class.py``):

- manifest evaluation rejects ``CAP_DEBUG_ATTACH`` without the class
  (NPS-011 §4.4 — creation-time, fail-closed);
- the class-conditional grant is centralized in ``CapabilityManager``
  (a grant without the declared class raises);
- the ptrace relaxation is a POLICY-BUILD parameter only (fence 2 —
  seccomp filters are one-shot; there is no runtime hook);
- debug-image staging writes the loopback-pinned
  ``debug-endpoints.json`` on the container's writable overlay at spawn
  (§5.5 req 4; wider binds need ``CAP_NETWORK_BIND``);
- the ``container_debug`` op family (info/attach/detach/bind) is the
  audited, capability-gated session marker (§5.5 req 1 + req 5).

What was missing is the UX on top: a real debugger session over that
channel. This module is that UX, and per DBG-001's Phase A discipline it
is **client-side composition only** — every privileged step it takes
flows through an op that already exists on the wire; no new daemon
surface, nothing new to authorize. The session flow:

1. ``container_debug(action="attach")`` — the audit-chained session
   marker (already refuses non-debug, non-RUNNING, unstaged, or
   ungranted containers; this module adds nothing around those gates).
2. The in-container debug server is the MANIFEST COMMAND ITSELF (the
   D7 design: the debugger runs inside the debuggee) — e.g.
   ``containers-run`` with
   ``command=["python3", "-m", "debugpy", "--listen", "127.0.0.1:5678",
   "app.py"]`` and ``debug_class: true``. This module never injects a
   program of its own; the endpoint it awaits comes from the attach
   op's reply (the staged marker's loopback values).
3. Connectivity: the endpoints are LOOPBACK-ONLY by D7 (§5.5 req 4).
   A container in its OWN network namespace (``network: true``)
   therefore is not reachable at 127.0.0.1 from the operator's host —
   the loopback inside that netns is a different loopback. There is no
   magical path around that: widening the bind is the ``debug-bind``
   op (audit-chained, ``CAP_NETWORK_BIND``-gated) plus a non-loopback
   endpoint. Until such a bind exists, own-netns containers REFUSE
   with that explanation (fail-closed — an error, never a silent
   degradation), and the validated default (``network: false`` — the
   container shares the host netns, so the staged loopback IS the
   operator's loopback) is the path that works.
4. The reachability wait CONNECTS AND KEEPS: the probe's socket IS
   the debug server's one client slot (pydevd binds to the first
   accepted connection), so the session record carries it for the
   DAP client to reuse — a reconnect would steal the slot.
5. The DAP bridge: ``dap_bridge()`` pipes the Debug Adapter Protocol's
   ``Content-Length``-framed messages between the IDE's stdio and the
   staged endpoint — the exact wire form VS Code and every DAP client
   already speak — so IDEs attach WITHOUT this CLI in the data path.
   It is a byte pipe, not a DAP implementation: it interprets nothing
   beyond the framing, filters nothing.
6. ``container_debug(action="detach")`` — the closing marker.

Trust notes (per NPC-002 §5.2, kept honest):
- The bridge terminates on EOF with a diagnostic on stderr — it cannot
  resynchronize a mis-framed stream (fail-closed).
- The session module NEVER grants capabilities itself: the grant is
  the daemon-side class-conditional path (NPS-011 §4.4) that fired at
  container creation; if the grant is absent, the attach op refuses
  and this module surfaces that refusal verbatim.
- No localhost-bypass exists by design: the netns posture is the
  enforced boundary, and this module treats own-netns + loopback-only
  as REFUSED, which is exactly the §5.5 req 4 default.

End-to-end validation record (2026-09-26, this host) — the recipe and
the three transport lessons that shaped this module:

1. The developer-mode manifest needs MORE than defaults +
   CAP_DEBUG_ATTACH: the in-container debugpy listener requires the
   socket families (``CAP_NETWORK_SOCKET`` for socket/connect,
   ``CAP_NETWORK_BIND`` for bind/listen/accept) — the seccomp baseline
   deliberately excludes them (they are capability-gated). Without
   them the server EPERMs on ``socket()`` and dies (honestly — the
   first attempt crashed the command). This is the D7-ledger
   debug-vs-prod divergence, in capability space, not image space.
2. THE REACHABILITY PROBE OWNS THE CLIENT SLOT: pydevd binds to the
   FIRST accepted connection. A connect-then-close probe wedges the
   server on the dead socket and the real DAP client's ``initialize``
   is never answered (silence, not an error). ``attach()`` therefore
   returns the KEPT probe socket (``_socket``) for the DAP client to
   REUSE — never reconnect.
3. DAP message quirk (pydevd): every request MUST carry an
   ``arguments`` key (even ``{}``) — pydevd deserializes via
   ``**dct["arguments"]`` and rejects a missing key outright
   ("missing 1 required positional argument: 'arguments'").
4. DAP sequencing (vscode#4902): ``initialize`` → ``attach`` → the
   adapter's ``initialized`` EVENT → ``setBreakpoints`` (before any
   other config request pydevd refuses: "Breakpoints may only be set
   after the launch request is received") → ``configurationDone``.
   Also: pydevd answers a debug-server request only after
   authentication; with no access token configured the first request
   works as-is (the class-conditional grant is the platform's gate).

Proof run (live container, manifest command = ``python3
-Xfrozen_modules=off -m debugpy --listen 127.0.0.1:5678 app.py``):
audit-chained attach marker → DAP initialize/attach over the kept
socket → initialized event → breakpoint at line 5 verified=True →
``configurationDone`` → stopped (reason=breakpoint) → variables →
``x == 40`` (pre-increment, i.e. execution paused BEFORE ``x += 2``)
→ continue → the program completes and prints ``AFTER_BP 42`` →
``debug_detach`` → hash chain verified with ``debug_class=true`` in
every entry. ``-Xfrozen_modules=off`` is REQUIRED: with frozen
modules the adapter goes silent under the container's seccomp
posture (its own warning recommends the flag).

References: DBG-001 (this is its last work item), NPS-021 v1.1.0 §4.8
and §5.5, NPS-011 v1.4.0 §4.4, AG decision log D7.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "DebugSessionError",
    "RefusedError",
    "UnreachableError",
    "BridgeProtocolError",
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DEFAULT_POLL_TIMEOUT_S",
    "DEFAULT_POLL_INTERVAL_S",
    "dap_endpoint_for",
    "read_dap_message",
    "write_dap_message",
    "dap_bridge",
    "attach",
    "detach",
]

DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_POLL_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 0.5

# The staged endpoints (container.py _setup_debug_staging) pin these
# loopback addresses; the CLI/daemon surface exposes the flavor names.
DEBUGPY_HOST = "127.0.0.1"
DEBUGPY_PORT = 5678
GDBSERVER_HOST = "127.0.0.1"
GDBSERVER_PORT = 1234


class DebugSessionError(Exception):
    """Base class: an attach session could not proceed."""


class RefusedError(DebugSessionError):
    """The daemon/manager refused (capability, state, staging, netns
    posture). The operator-facing message is the refusal itself."""


class UnreachableError(DebugSessionError):
    """The staged endpoint never accepted a connection within the
    session's wait budget."""


class BridgeProtocolError(DebugSessionError):
    """The DAP stream ended or was mis-framed (fail-closed)."""


def dap_endpoint_for(debugger: str,
                     endpoints: Optional[Dict[str, Any]] = None,
                     ) -> Tuple[str, int]:
    """The (host, port) for a debugger flavor.

    ``endpoints`` — the attach/info op reply's staged endpoint map
    (``{"debugpy": "127.0.0.1:5678", ...}``); when absent, the module's
    mirrored constants are used (they pin the same values the staging
    writes). Unknown flavors raise — the flavor set is exactly the D7
    set.
    """
    if debugger == "debugpy":
        default = (DEBUGPY_HOST, DEBUGPY_PORT)
    elif debugger == "gdbserver":
        default = (GDBSERVER_HOST, GDBSERVER_PORT)
    else:
        raise ValueError(f"unknown debugger flavor: {debugger!r} "
                         "(D7 ships debugpy|gdbserver)")
    if endpoints:
        raw = endpoints.get(debugger)
        if isinstance(raw, str) and ":" in raw:
            host, _, port = raw.rpartition(":")
            try:
                return (host or default[0], int(port))
            except ValueError:
                pass  # malformed marker value: fall back to the pinned
    return default


# ----------------------------------------------------------------------
# DAP wire framing (the byte pipe — no interpretation beyond framing)
# ----------------------------------------------------------------------

def read_dap_message(recv: Callable[[int], bytes]) -> Dict[str, Any]:
    """Read ONE DAP message from ``recv(n) -> bytes``.

    DAP framing (the protocol every DAP client/adapter speaks):
    ``Content-Length: <n>\\r\\n\\r\\n<n bytes of JSON>``. Header block
    may carry other headers (e.g. Content-Type) — read and discarded.

    ``recv`` may return FEWER or MORE bytes than asked (both socket
    ``recv`` and buffered stream reads legitimately do); over-returns
    are buffered for the next read. Raises BridgeProtocolError on EOF
    or a malformed frame (the bridge cannot resynchronize a broken
    stream — fail-closed).
    """
    buf = bytearray()

    def read_exact(n: int) -> bytes:
        while len(buf) < n:
            chunk = recv(4096)
            if not chunk:
                raise BridgeProtocolError("DAP stream closed (EOF)")
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    content_length: Optional[int] = None
    while True:
        line = bytearray()
        while True:
            ch = read_exact(1)
            if ch == b"\n":
                break
            line += ch
        text = bytes(line).decode("ascii", errors="replace").strip()
        if not text:
            break  # blank line: end of headers
        name, _, value = text.partition(":")
        if name.strip().lower() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError as e:
                raise BridgeProtocolError(
                    f"malformed Content-Length header: {text!r}") from e
    if content_length is None:
        raise BridgeProtocolError("DAP frame missing Content-Length")
    body = read_exact(content_length)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BridgeProtocolError(f"malformed DAP JSON body: {e}") from e


def write_dap_message(send: Callable[[bytes], Any],
                      message: Dict[str, Any]) -> None:
    """Write ONE DAP message with the canonical ``Content-Length``
    framing (compact JSON, exactly what adapters emit)."""
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    send(b"Content-Length: %d\r\n\r\n" % len(body) + body)


def _connect(host: str, port: int, timeout_s: float) -> socket.socket:
    try:
        return socket.create_connection((host, port), timeout=timeout_s)
    except OSError as e:
        raise UnreachableError(
            f"staged endpoint {host}:{port} unreachable: {e} "
            "(is the in-container debug server listening? is the "
            "container network:false — loopback-shared?)") from e


def dap_bridge(sock: socket.socket,
               stdin_read: Callable[[int], bytes],
               stdout_write: Callable[[bytes], Any],
               ) -> None:
    """Bridge DAP between stdio (the IDE's adapter transport) and the
    staged endpoint of an in-container debug server (``sock`` is
    already connected).

    Two pumps, both framing-only — DAP payloads are never inspected:

    - IDE -> container: read a message from stdin, write it to the
      socket. Ends when the IDE closes stdin (the normal session end)
      or the frame is malformed (BridgeProtocolError — fail-closed).
    - container -> IDE: read a message from the socket, write it to
      stdout. Ends on the adapter's EOF or a malformed frame.

    DAP is bidirectional (the adapter emits events unsolicited), so the
    pumps run on separate threads; the call blocks until either side
    closes, then closes the socket (which unblocks the other pump's
    recv — a blocked read on a closed socket raises immediately). The
    IDE-visible stdout writes are the caller's ``stdout_write`` (the
    CLI wrapper flushes there).
    """
    done = threading.Event()

    def stop() -> None:
        done.set()
        # Unblock the other pump: shutdown wakes a blocked recv/sendall
        # (OSError), close releases the fd. Both tolerate being wrong.
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def ide_to_container() -> None:
        try:
            while not done.is_set():
                message = read_dap_message(stdin_read)
                write_dap_message(sock.sendall, message)
        except (BridgeProtocolError, OSError):
            pass  # IDE closed stdin or the socket died: session over
        finally:
            stop()

    thread = threading.Thread(target=ide_to_container,
                              name="dap-bridge-ide", daemon=True)
    thread.start()
    try:
        while not done.is_set():
            reply = read_dap_message(sock.recv)
            write_dap_message(stdout_write, reply)
    except (BridgeProtocolError, OSError):
        pass  # adapter closed its side, or the other pump stopped us
    finally:
        stop()
        thread.join(timeout=5.0)


# ----------------------------------------------------------------------
# The session orchestrator (client-side composition of existing ops)
# ----------------------------------------------------------------------

def attach(container_call: Callable[[str, Dict[str, Any]], Dict[str, Any]],
           container_id: str,
           debugger: str = "debugpy",
           poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
           poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
           connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
           probe: Optional[Callable[[str, int, float], Any]] = None,
           monotonic: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep,
           ) -> Dict[str, Any]:
    """Run one attach session against ``container_id``.

    ``container_call(op, payload) -> reply`` is the operator's existing
    IPC path (the CLI's ``call_daemon`` over its composed payload).
    Every call made here is an op that already exists on the wire:

    - ``container_debug`` (action attach): the audit-chained marker —
      its refusal is surfaced verbatim (capability/class/state/staging
      gates live in the manager, unchanged);
    - ``container_list``: to learn the container's network posture. An
      own-netns container cannot be reached at the LOOPBACK endpoint —
      refused with the explanation, per §5.5 req 4, until a real wider
      bind exists (no bypass is manufactured here).

    The endpoint comes from the attach reply (the staged marker's
    loopback values, falling back to the module's pinned constants).

    THE REACHABILITY PROBE OWNS THE CLIENT SLOT: an in-container debug
    server accepts ONE DAP client (pydevd binds to the first accepted
    connection), so ``probe`` must return a KEPT, OPEN connection —
    the default connect-and-hold — never a connect-then-close (a
    closing probe wedges pydevd on the dead socket and the real DAP
    client's ``initialize`` is never answered — found end-to-end,
    2026-09-26). The session record carries that connection as
    ``_socket``: the DAP client (the IDE bridge, the proof driver)
    REUSES it for the handshake. ``probe`` implementations that only
    answer True/False (tests) leave ``_socket`` absent.

    On timeout the marker is released with a best-effort ``detach`` and
    ``UnreachableError`` carries the exact remediation (start the
    server via the manifest command).

    Returns the session record (endpoint, audit entry, kept socket).
    """
    attach_resp = container_call("container_debug", {
        "service": "control", "op": "container_debug",
        "container_id": container_id, "action": "attach",
        "debugger": debugger,
    })
    if not attach_resp.get("ok"):
        raise RefusedError(
            "attach refused: %s" % (attach_resp.get("error", "?"),))

    # Loopback posture check (§5.5 req 4): the staged endpoint is
    # 127.0.0.1 INSIDE the container. network=true puts that loopback
    # in a private netns the operator cannot reach; refuse honestly
    # rather than spin on an unreachable port.
    list_resp = container_call("container_list",
                               {"service": "control",
                                "op": "container_list"})
    posture = None
    for c in (list_resp.get("containers") or []):
        if c.get("id") == container_id:
            posture = c
            break
    if posture is not None and posture.get("network"):
        host, port = dap_endpoint_for(
            debugger, attach_resp.get("endpoints"))
        # Release the just-opened marker: a session that this module
        # refuses must not dangle open (fail-closed, audited both ways).
        try:
            container_call("container_debug", {
                "service": "control", "op": "container_debug",
                "container_id": container_id, "action": "detach",
                "debugger": debugger,
            })
        except Exception:  # noqa: BLE001 — the refusal below is the point
            pass
        raise RefusedError(
            "container %s runs in its OWN network namespace: the staged "
            "endpoint %s:%s is loopback-ONLY inside that namespace "
            "(NPS-021 §5.5 req 4) and unreachable from the operator "
            "host. Re-run with network:false (the validated default: "
            "the container shares the host loopback), or bind wider "
            "via debug-bind (CAP_NETWORK_BIND, audit-chained) once a "
            "non-loopback endpoint exists. The attach marker was "
            "released." % (container_id, host, port))

    host, port = dap_endpoint_for(debugger, attach_resp.get("endpoints"))
    if probe is None:
        def probe(h: str, p: int, timeout: float) -> Any:
            # Connect AND KEEP: the returned socket is the debug
            # server's client slot (see the docstring's probe rule).
            return socket.create_connection((h, p), timeout=timeout)

    started = monotonic()
    reachable = False
    held_socket = None
    deadline = started + poll_timeout_s
    while monotonic() < deadline:
        try:
            held = probe(host, port, connect_timeout_s)
        except OSError:
            held = None
        if held:
            reachable = True
            # A kept connection (a socket-like with recv/close — duck-
            # typed so doubles/tests can also supply one) is the client
            # slot; a bare True is a probe that only answered reachability.
            if held is not True and hasattr(held, "recv"):
                held_socket = held
            break
        sleep(poll_interval_s)

    if not reachable:
        # Best-effort detach so the session marker does not dangle.
        try:
            container_call("container_debug", {
                "service": "control", "op": "container_debug",
                "container_id": container_id, "action": "detach",
                "debugger": debugger,
            })
        except Exception:  # noqa: BLE001 — the report below is the point
            pass
        raise UnreachableError(
            "no in-container %s server appeared on %s:%s within %ss — "
            "start it via the manifest command (e.g. containers-run "
            "with command=[python3, -m, debugpy, --listen, %s:%s, "
            "app.py] and debug_class:true); the attach marker was "
            "released." % (debugger, host, port, int(poll_timeout_s),
                           host, port))

    record = {
        "ok": True,
        "container_id": container_id,
        "debugger": debugger,
        "endpoint": {"host": host, "port": port},
        "loopback_only": bool(attach_resp.get("loopback_only", True)),
        "attach_audit": attach_resp.get("audit", {}),
        "waited_s": round(monotonic() - started, 3),
    }
    if held_socket is not None:
        # The KEPT client slot: the DAP client reuses this connection
        # (reconnecting would steal the server's one client slot —
        # pydevd answers only the first accepted socket).
        record["_socket"] = held_socket
    return record


def detach(container_call: Callable[[str, Dict[str, Any]], Dict[str, Any]],
           container_id: str,
           debugger: str = "debugpy",
           ) -> Dict[str, Any]:
    """Close the session marker (the audit-chained detach)."""
    resp = container_call("container_debug", {
        "service": "control", "op": "container_debug",
        "container_id": container_id, "action": "detach",
        "debugger": debugger,
    })
    if not resp.get("ok"):
        raise RefusedError(
            "detach refused: %s" % (resp.get("error", "?"),))
    return resp


def main(argv: Optional[List[str]] = None) -> int:
    """``python3 debug_attach.py`` — the stdio DAP bridge standalone:
    ``python3 debug_attach.py <host> <port>``. The IDE path; the CLI
    wrapper (``nyrqisctl debug dap-bridge``) resolves the endpoint via
    the daemon and lands here. Exits 0 when the session ends cleanly
    (IDE closed stdin), 1 on an unreachable/broken stream, 2 on usage.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: debug_attach.py <host> <port>  "
              "(stdio DAP bridge)", file=sys.stderr)
        return 2

    out = sys.stdout.buffer

    def stdout_write(data: bytes) -> Any:
        written = out.write(data)
        out.flush()
        return written

    try:
        sock = _connect(argv[0], int(argv[1]), DEFAULT_CONNECT_TIMEOUT_S)
        dap_bridge(sock, sys.stdin.buffer.read1, stdout_write)
    except (UnreachableError, BridgeProtocolError) as e:
        print(f"debug dap-bridge: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
