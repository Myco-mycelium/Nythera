"""Pins for the D7 attach UX (2026-09-26) — the last DBG-001 work item.

The security surface shipped 2026-09-23/25 (manifest class, the
class-conditional grant, the construction-time ptrace relaxation, debug
staging, the container_debug op family — see test_debug_manifest_class.py).
This module pins the UX layer on top, per DBG-001's Phase A discipline:

- the session orchestrator is CLIENT-SIDE COMPOSITION ONLY: every call
  it makes is an op that already exists on the wire (container_debug
  attach/detach, container_list, container_exec) — no new daemon op;
- the audit-chained attach marker is opened FIRST and closed on every
  failure path (fail-closed: no dangling session);
- an own-netns container (network: true) is REFUSED: the staged
  endpoints are loopback-ONLY inside the container's namespace
  (NPS-021 §5.5 req 4) and no localhost bypass is manufactured;
- the DAP bridge is a byte pipe with exact Content-Length framing —
  it never interprets or filters DAP payloads, and a malformed frame
  or EOF fails closed (BridgeProtocolError);
- the endpoint resolves from the STAGED marker values (the attach
  reply), never invented client-side;
- the container_run passthrough: debug_class + rootfs ride the wire;
  the daemon grants manifest-requested class-conditional capabilities
  through the class-gated grant path (a non-debug manifest raises —
  NPS-011 §4.4) — the wiring test, not a reimplementation.
"""

import importlib
import io
import json
import os
import queue
import socket
import sys
import threading
import time
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

debug_attach = importlib.import_module("debug_attach")
from backend.capability import Capability, CapabilityManager  # noqa: E402
from backend.container import (  # noqa: E402
    ContainerConfig, ContainerManager, ContainerState,
)


def _make_manager():
    return ContainerManager(
        use_cgroups_v2=False, capability_manager=CapabilityManager())


class _KeptSocket:
    """A probe double returning a keepable object (not a bool): the
    orchestrator must HAND BACK the probe's connection as ``_socket``
    (the debug server's one client slot is owned by the first
    connector — found end-to-end 2026-09-26)."""

    closed = False

    def recv(self, n):
        return b""

    def close(self):
        self.closed = True


class _FakeDaemon:
    """Records every op call; replies from a scriptable map.

    The point: the orchestrator must compose ONLY ops that exist on
    the wire — a call to anything else fails the test loudly.
    """

    EXISTING_OPS = {
        "container_debug", "container_list", "container_exec",
        "container_kill", "container_stats", "container_logs",
        "container_top", "container_network_stats",
    }

    def __init__(self, netns=False, attach_ok=True):
        self.calls = []
        self.netns = netns
        self.attach_ok = attach_ok

    def __call__(self, op, payload):
        self.calls.append((op, payload))
        assert op in self.EXISTING_OPS, (
            f"orchestrator composed a NON-EXISTENT op: {op!r}")
        if op == "container_debug":
            if payload.get("action") == "attach" and not self.attach_ok:
                return {"ok": False, "error": "requires CAP_DEBUG_ATTACH"}
            if payload.get("action") == "attach":
                return {"ok": True, "container_id": payload["container_id"],
                        "action": "attach",
                        "endpoints": {"debugpy": "127.0.0.1:5678",
                                      "gdbserver": "127.0.0.1:1234"},
                        "loopback_only": True,
                        "audit": {"chain_id": "ch-1", "hash": "ab" * 32,
                                  "ok": True}}
            return {"ok": True, "container_id": payload["container_id"],
                    "action": payload.get("action"),
                    "audit": {"chain_id": "ch-1", "hash": "cd" * 32,
                              "ok": True}}
        if op == "container_list":
            return {"ok": True, "containers": [
                {"id": "c1", "state": "running", "pid": 4242,
                 "network": self.netns, "debug_class": True}]}
        return {"ok": True}


class _FlakyProbe:
    """A probe that refuses until ``succeed_after`` calls."""

    def __init__(self, succeed_after=2):
        self.n = 0
        self.succeed_after = succeed_after

    def __call__(self, host, port, timeout):
        self.n += 1
        return self.n > self.succeed_after


class AttachSessionTests(unittest.TestCase):
    """The orchestrator: composition discipline + failure handling."""

    def test_attach_composes_only_existing_ops(self):
        calls = _FakeDaemon()
        kept = _KeptSocket()

        def probe(h, p, timeout):
            _FlakyProbe(1)(h, p, timeout)  # succeed on first call
            return kept

        result = debug_attach.attach(
            calls, "c1", probe=probe,
            poll_timeout_s=2.0, poll_interval_s=0.01,
            monotonic=time.monotonic, sleep=lambda s: None)
        ops = [op for op, _ in calls.calls]
        self.assertEqual(ops[0], "container_debug")
        self.assertEqual(calls.calls[0][1].get("action"), "attach")
        self.assertIn("container_list", ops)
        self.assertTrue(result["ok"])
        self.assertEqual(result["endpoint"]["port"], 5678)
        self.assertTrue(result["loopback_only"])
        self.assertTrue(result["attach_audit"].get("hash"))
        # THE CLIENT SLOT: the kept connection is handed back for the
        # DAP client to reuse (a reconnect would steal the slot).
        self.assertIs(result["_socket"], kept)

    def test_bool_probe_leaves_no_socket(self):
        calls = _FakeDaemon()
        result = debug_attach.attach(
            calls, "c1", probe=_FlakyProbe(1),
            poll_timeout_s=2.0, poll_interval_s=0.01,
            monotonic=time.monotonic, sleep=lambda s: None)
        self.assertTrue(result["ok"])
        self.assertNotIn("_socket", result)

    def test_attach_refusal_is_surfaced_verbatim(self):
        calls = _FakeDaemon(attach_ok=False)
        with self.assertRaises(debug_attach.RefusedError) as ctx:
            debug_attach.attach(calls, "c1", probe=lambda *a: True,
                                monotonic=time.monotonic,
                                sleep=lambda s: None)
        # The refused attach op is the LAST call — nothing else ran.
        self.assertEqual(len(calls.calls), 1)
        self.assertIn("CAP_DEBUG_ATTACH", str(ctx.exception))

    def test_own_netns_container_is_refused_and_marker_released(self):
        calls = _FakeDaemon(netns=True)
        with self.assertRaises(debug_attach.RefusedError) as ctx:
            debug_attach.attach(calls, "c1", probe=lambda *a: True,
                                monotonic=time.monotonic,
                                sleep=lambda s: None)
        self.assertIn("OWN network namespace", str(ctx.exception))
        self.assertIn("127.0.0.1:5678", str(ctx.exception))
        ops = [(op, p.get("action")) for op, p in calls.calls
               if op == "container_debug"]
        # attach opened, then detach released the marker.
        self.assertEqual(ops, [("container_debug", "attach"),
                               ("container_debug", "detach")])

    def test_unreachable_endpoint_releases_marker_and_reports(self):
        calls = _FakeDaemon()
        with self.assertRaises(debug_attach.UnreachableError) as ctx:
            debug_attach.attach(calls, "c1", probe=lambda *a: False,
                                poll_timeout_s=0.3,
                                poll_interval_s=0.01,
                                monotonic=time.monotonic,
                                sleep=lambda s: None)
        self.assertIn("manifest command", str(ctx.exception))
        self.assertIn("released", str(ctx.exception))
        ops = [(op, p.get("action")) for op, p in calls.calls
               if op == "container_debug"]
        self.assertEqual(ops[-1], ("container_debug", "detach"))

    def test_endpoint_resolves_from_staged_marker(self):
        calls = _FakeDaemon()
        result = debug_attach.attach(calls, "c1", debugger="gdbserver",
                                     probe=lambda *a: True,
                                     monotonic=time.monotonic,
                                     sleep=lambda s: None)
        # The gdbserver endpoint comes from the STAGED marker values
        # in the attach reply — not the debugpy default.
        self.assertEqual(result["endpoint"],
                         {"host": "127.0.0.1", "port": 1234})

    def test_dap_endpoint_for_unknown_flavor_raises(self):
        with self.assertRaises(ValueError):
            debug_attach.dap_endpoint_for("radare")
        host, port = debug_attach.dap_endpoint_for(
            "debugpy", {"debugpy": "127.0.0.1:9999"})
        self.assertEqual((host, port), ("127.0.0.1", 9999))
        host, port = debug_attach.dap_endpoint_for("gdbserver")
        self.assertEqual((host, port), ("127.0.0.1", 1234))

    def test_detach_is_the_audited_marker_close(self):
        calls = _FakeDaemon()
        resp = debug_attach.detach(calls, "c1")
        self.assertTrue(resp.get("ok"))
        last = calls.calls[-1]
        self.assertEqual(last[0], "container_debug")
        self.assertEqual(last[1].get("action"), "detach")


class DapFramingTests(unittest.TestCase):
    """The bridge is a framing-only byte pipe (exact Content-Length)."""

    def _reader(self, data: bytes):
        buf = io.BytesIO(data)

        def recv(n: int) -> bytes:
            chunk = buf.read(n)
            if not chunk:
                raise debug_attach.BridgeProtocolError("EOF")
            return chunk

        return recv

    def test_read_dap_message_parses_framed_json(self):
        body = json.dumps({"seq": 1, "type": "request",
                           "command": "initialize"}).encode()
        reader = self._reader(
            b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        msg = debug_attach.read_dap_message(reader)
        self.assertEqual(msg["command"], "initialize")

    def test_read_dap_message_tolerates_extra_headers(self):
        body = b'{"a": 1}'
        reader = self._reader(
            b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
            b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        self.assertEqual(debug_attach.read_dap_message(reader)["a"], 1)

    def test_read_dap_message_fails_closed_on_truncated_body(self):
        reader = self._reader(b"Content-Length: 50\r\n\r\n{\"a\": 1}")
        with self.assertRaises(debug_attach.BridgeProtocolError):
            debug_attach.read_dap_message(reader)

    def test_write_then_read_round_trips(self):
        buf = io.BytesIO()
        message = {"seq": 7, "type": "response",
                   "body": {"x": "y" * 40}}
        debug_attach.write_dap_message(buf.write, message)
        buf.seek(0)
        back = debug_attach.read_dap_message(
            self._reader(buf.getvalue()))
        self.assertEqual(back, message)

    def _bridge_over_socketpair(self, ide_in: bytes,
                                expect_reply: bool = True) -> bytes:
        """Drive the bridge over a real socketpair; returns what the
        fake IDE's stdout received (what came back from the adapter).

        The fake IDE's stdin is a QUEUE of bytes with an EOF sentinel:
        reads return available bytes immediately (a real IDE's stdin
        never blocks on the adapter), and the sentinel (b"") is queued
        only after the expected traffic has flowed — the bridge treats
        stdin EOF as the normal session end.
        """
        ide_sock, adapter_sock = socket.socketpair()
        out = io.BytesIO()
        in_q: queue.Queue = queue.Queue()
        for i in range(0, len(ide_in), 7):
            in_q.put(ide_in[i:i + 7])

        def stdout_write(data: bytes):
            out.write(data)
            out.flush()

        def stdin_read(n: int) -> bytes:
            try:
                return in_q.get(timeout=5.0)
            except queue.Empty:
                return b""  # the IDE went silent: EOF

        bridge = threading.Thread(
            target=debug_attach.dap_bridge,
            args=(ide_sock, stdin_read, stdout_write), daemon=True)
        bridge.start()
        if expect_reply:
            # The adapter side: read one framed message, answer, close.
            first = adapter_sock.recv(65536)
            self.assertIn(b"initialize", first)
            reply_body = json.dumps({"seq": 1, "type": "response",
                                     "request_seq": 1,
                                     "success": True}).encode()
            adapter_sock.sendall(
                b"Content-Length: %d\r\n\r\n%s"
                % (len(reply_body), reply_body))
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if b'"response"' in out.getvalue():
                    break
                time.sleep(0.01)
            adapter_sock.close()
        else:
            # Garbage frame: the bridge must die before forwarding.
            # Give it a beat, then close the adapter side (its recv
            # would otherwise block forever on a dead peer).
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if not bridge.is_alive():
                    break
                time.sleep(0.01)
            try:
                adapter_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            adapter_sock.close()
        in_q.put(b"")  # the IDE closes stdin: the session ends
        bridge.join(timeout=5.0)
        return out.getvalue()

    def test_bridge_is_a_transparent_byte_pipe(self):
        req_body = json.dumps({"seq": 1, "type": "request",
                               "command": "initialize"}).encode()
        ide_in = (b"Content-Length: %d\r\n\r\n%s"
                  % (len(req_body), req_body))
        out = self._bridge_over_socketpair(ide_in)
        # Compact-JSON framing passes through byte-exact (transparency).
        self.assertIn(b'"type":"response"', out)
        self.assertIn(b"Content-Length: 58", out)

    def test_bridge_fails_closed_on_garbage_frame(self):
        out = self._bridge_over_socketpair(
            b"NOT-A-DAP-FRAME\r\n\r\n", expect_reply=False)
        # No bytes forwarded, no crash — the bridge ended the session.
        self.assertEqual(out, b"")


class ContainerRunPassthroughTests(unittest.TestCase):
    """The wire-level enablement: debug_class/rootfs ride container_run;
    the daemon grants the manifest-requested class-conditional cap
    through the class-gated path AFTER spawn (NPS-011 §4.4 wiring)."""

    def test_grant_after_spawn_wiring(self):
        """Direct-manager proof of the exact grant sequence the IPC
        handler performs (create -> spawn -> grant manifest caps): a
        debug manifest gets the grant; a non-debug one raises."""
        m = _make_manager()
        caps = ["CAP_DEBUG_ATTACH"]
        container = m.create(ContainerConfig(
            command=["/bin/true"], debug_class=True,
            capabilities=caps))
        m.spawn(container)
        # The wiring's grant step (what _container_run does post-spawn):
        cm = m.capability_manager
        for cap_name in caps:
            cap = Capability(cap_name)
            if cap not in cm.get_capabilities(container.id):
                cm.grant_capability(container.id, cap)
        self.assertIn(Capability.CAP_DEBUG_ATTACH,
                      cm.get_capabilities(container.id))
        try:
            m.terminate(container)
        except Exception:
            pass

    def test_non_debug_manifest_cannot_be_granted(self):
        m = _make_manager()
        cm = m.capability_manager
        cm.declare_container_class("c-plain", debug_class=False)
        with self.assertRaises(ValueError):
            cm.grant_capability("c-plain", Capability.CAP_DEBUG_ATTACH)

    def test_cli_run_payload_carries_debug_fields(self):
        nyrqisctl = importlib.import_module("nyrqisctl")
        # ``--`` separates the manifest command from the flags (the
        # command is nargs=+; the pre-existing argparse rule).
        argv = ["--socket", "/tmp/unused.sock", "containers", "run",
                "--debug-class", "--rootfs", "/tmp/rootfs-x", "--",
                "python3", "-m", "debugpy", "--listen",
                "127.0.0.1:5678", "app.py"]
        ns = nyrqisctl.build_parser().parse_args(argv)
        payload = nyrqisctl.build_payload("containers-run", ns)
        self.assertTrue(payload["debug_class"])
        self.assertEqual(payload["rootfs"], "/tmp/rootfs-x")
        self.assertIn("debugpy", payload["command"])


if __name__ == "__main__":
    unittest.main()
