"""
Tests for the LAN Share app — protocol, discovery, and end-to-end transfer.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

# Make the flat top-level modules importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import protocol
import transfer
from discovery import Discovery, Peer
from utils import compute_sha256, Device


# ─────────────────────────────────────────────────────────────────────────────
# protocol.py — TCP framing + UDP encode/decode
# ─────────────────────────────────────────────────────────────────────────────

class TestProtocol(unittest.TestCase):

    def _socket_pair(self):
        """Return a connected (client_sock, server_sock) pair via loopback."""
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        cli = socket.socket(); cli.connect(("127.0.0.1", port))
        conn, _ = srv.accept()
        srv.close()
        return cli, conn

    def test_round_trip_metadata(self):
        cli, srv = self._socket_pair()
        try:
            protocol.send_msg(cli, protocol.METADATA,
                              filename="x.bin", filesize=999, sha256="abc")
            msg = protocol.recv_msg(srv)
            self.assertEqual(msg["type"],     protocol.METADATA)
            self.assertEqual(msg["filename"], "x.bin")
            self.assertEqual(msg["filesize"], 999)
        finally:
            cli.close(); srv.close()

    def test_round_trip_data_with_payload(self):
        cli, srv = self._socket_pair()
        try:
            payload = bytes(range(256))
            protocol.send_msg(cli, protocol.DATA,
                              payload_size=len(payload), payload=payload)
            msg = protocol.recv_msg(srv)
            self.assertEqual(msg["type"],    protocol.DATA)
            self.assertEqual(msg["payload"], payload)
        finally:
            cli.close(); srv.close()

    def test_round_trip_request(self):
        cli, srv = self._socket_pair()
        try:
            manifest = [{"name": "a", "size": 1, "sha256": ""}]
            protocol.send_msg(cli, protocol.REQUEST,
                              sender_name="A", manifest=manifest, total_size=1)
            msg = protocol.recv_msg(srv)
            self.assertEqual(msg["type"],     protocol.REQUEST)
            self.assertEqual(msg["manifest"], manifest)
        finally:
            cli.close(); srv.close()

    def test_udp_encode_decode(self):
        packet = protocol.encode_udp(protocol.HELLO,
                                     device_id="abc", name="A", tcp_port=1, version=2)
        msg = protocol.decode_udp(packet)
        self.assertIsNotNone(msg)
        self.assertEqual(msg["type"],     protocol.HELLO)
        self.assertEqual(msg["tcp_port"], 1)

    def test_udp_decode_garbage_returns_none(self):
        self.assertIsNone(protocol.decode_udp(b"not json"))
        self.assertIsNone(protocol.decode_udp(b'{"no_type":true}'))


# ─────────────────────────────────────────────────────────────────────────────
# discovery.py — two Discovery instances on the same machine find each other
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscovery(unittest.TestCase):
    """
    Critical same-machine scenario: two Discovery instances must each see
    the other.  Tests use a dedicated UDP port + multicast group to avoid
    clashing with a live app instance.
    """

    TEST_UDP_PORT  = 25_555
    TEST_MCAST     = "239.42.42.99"

    def test_two_discoveries_find_each_other(self):
        a = Device(device_id="dev-a", name="A")
        b = Device(device_id="dev-b", name="B")
        d_a = Discovery(device=a, tcp_port=5001,
                        udp_port=self.TEST_UDP_PORT, multicast_group=self.TEST_MCAST)
        d_b = Discovery(device=b, tcp_port=5002,
                        udp_port=self.TEST_UDP_PORT, multicast_group=self.TEST_MCAST)
        try:
            d_a.start(); d_b.start()
            deadline = time.time() + 5.0
            while time.time() < deadline and (
                len(d_a.peers) == 0 or len(d_b.peers) == 0
            ):
                time.sleep(0.1)
            self.assertEqual(len(d_a.peers), 1, "A never saw B")
            self.assertEqual(len(d_b.peers), 1, "B never saw A")
        finally:
            d_a.stop(); d_b.stop()

    def test_same_device_id_different_tcp_ports_still_discover(self):
        """
        Same-machine real-world case: both instances may share the same
        persisted device_id. They must still discover each other when their
        TCP listener ports differ.
        """
        shared_id = "shared-dev-id"
        a = Device(device_id=shared_id, name="A")
        b = Device(device_id=shared_id, name="B")
        d_a = Discovery(device=a, tcp_port=5011,
                        udp_port=self.TEST_UDP_PORT + 1, multicast_group=self.TEST_MCAST)
        d_b = Discovery(device=b, tcp_port=5013,
                        udp_port=self.TEST_UDP_PORT + 1, multicast_group=self.TEST_MCAST)
        try:
            d_a.start(); d_b.start()
            deadline = time.time() + 5.0
            while time.time() < deadline and (
                len(d_a.peers) == 0 or len(d_b.peers) == 0
            ):
                time.sleep(0.1)
            self.assertGreaterEqual(len(d_a.peers), 1, "A never saw B (same device_id case)")
            self.assertGreaterEqual(len(d_b.peers), 1, "B never saw A (same device_id case)")
        finally:
            d_a.stop(); d_b.stop()


# ─────────────────────────────────────────────────────────────────────────────
# transfer.py — full end-to-end file transfer over TCP loopback
# ─────────────────────────────────────────────────────────────────────────────

def _make_temp_file(size_bytes: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".bin", prefix="ft_test_")
    with os.fdopen(fd, "wb") as f:
        if size_bytes > 0:
            pattern = bytes(range(256))
            full, rem = divmod(size_bytes, 256)
            f.write(pattern * full); f.write(pattern[:rem])
    return path


class _LoopbackServer:
    """Minimal TCP server that delegates to ``transfer.serve_forever``."""

    def __init__(self, save_dir: str, accept_callback=lambda req: True):
        # Pick any free port
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.save_dir = save_dir
        self.received: list[str] = []
        self.stop_flag = threading.Event()

        self._thread = threading.Thread(
            target=transfer.serve_forever,
            kwargs=dict(
                port=self.port,
                accept_callback=accept_callback,
                on_log=lambda m: None,
                on_progress=lambda d, t, s: None,
                on_file_saved=self.received.append,
                save_dir=save_dir,
                stop_flag=self.stop_flag,
            ),
            daemon=True,
        )
        self._thread.start()
        time.sleep(0.2)  # let it bind

    def stop(self):
        self.stop_flag.set()


class TestTransfer(unittest.TestCase):

    def setUp(self):
        self.save_dir = tempfile.mkdtemp(prefix="ft_recv_")
        self.server   = _LoopbackServer(save_dir=self.save_dir)
        self.tmp:     list[str] = []

    def tearDown(self):
        self.server.stop()
        for p in self.tmp:
            try: os.remove(p)
            except OSError: pass

    def test_single_file(self):
        src = _make_temp_file(1024); self.tmp.append(src)
        ok = transfer.send_files("127.0.0.1", self.server.port, [src],
                                 sender_name="t", sender_id="t")
        self.assertTrue(ok)
        dst = os.path.join(self.save_dir, os.path.basename(src))
        self.assertEqual(compute_sha256(src), compute_sha256(dst))

    def test_multi_file(self):
        srcs = [_make_temp_file(2_048), _make_temp_file(8_192), _make_temp_file(0)]
        self.tmp.extend(srcs)
        ok = transfer.send_files("127.0.0.1", self.server.port, srcs,
                                 sender_name="t", sender_id="t")
        self.assertTrue(ok)
        for s in srcs:
            dst = os.path.join(self.save_dir, os.path.basename(s))
            self.assertEqual(compute_sha256(s), compute_sha256(dst))

    def test_empty_file(self):
        src = _make_temp_file(0); self.tmp.append(src)
        ok = transfer.send_files("127.0.0.1", self.server.port, [src],
                                 sender_name="t", sender_id="t")
        self.assertTrue(ok)

    def test_chunk_boundary(self):
        from config import CHUNK_SIZE
        src = _make_temp_file(CHUNK_SIZE + 1); self.tmp.append(src)  # exactly two DATA messages
        ok = transfer.send_files("127.0.0.1", self.server.port, [src],
                                 sender_name="t", sender_id="t")
        self.assertTrue(ok)

    def test_receiver_rejects(self):
        self.server.stop()
        self.server = _LoopbackServer(save_dir=self.save_dir,
                                      accept_callback=lambda req: False)
        src = _make_temp_file(512); self.tmp.append(src)
        ok = transfer.send_files("127.0.0.1", self.server.port, [src],
                                 sender_name="t", sender_id="t")
        self.assertFalse(ok)

    def test_cancellation(self):
        src = _make_temp_file(8 * 1024 * 1024); self.tmp.append(src)
        cancel = threading.Event()

        def on_progress(d, t, s):
            if d > 0: cancel.set()

        ok = transfer.send_files("127.0.0.1", self.server.port, [src],
                                 sender_name="t", sender_id="t",
                                 on_progress=on_progress, cancel_flag=cancel)
        self.assertFalse(ok)

    def test_connection_refused(self):
        src = _make_temp_file(512); self.tmp.append(src)
        ok = transfer.send_files("127.0.0.1", 1, [src],   # port 1 is privileged & nobody listens
                                 sender_name="t", sender_id="t")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
