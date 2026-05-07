"""
Device discovery on the local network using UDP multicast + broadcast fallback.

NETWORKING NOTES (for the discussion)
=====================================

Why UDP, not TCP?
    Discovery is "I want to announce myself to anyone who will listen".
    There is no recipient yet, no connection to set up.  TCP requires
    connect/accept, which means you must already know each other's IP.
    UDP is connectionless: you just send a datagram into the void.

Why multicast + broadcast fallback?
    - Multicast is primary because same-machine multi-process discovery is
      more reliable on Windows.
    - Some routers/guest networks suppress multicast, so we also send the
      same HELLO packet to limited broadcast (255.255.255.255) as fallback.

The protocol:
    HELLO  every BEACON_INTERVAL seconds → "I'm here, I'm at IP X port Y"
    BYE    once on shutdown            → "I'm leaving, drop me from your list"

Receiver-side TTL:
    Peers expire after PEER_TTL seconds without a HELLO, so a laptop
    that goes to sleep silently disappears from everyone's list.
"""

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import protocol
from config import (
    BEACON_INTERVAL,
    MULTICAST_GROUP,
    MULTICAST_TTL,
    PEER_TTL,
    PROTOCOL_VERSION,
    UDP_PORT,
)
from utils import Device


# ── Peer dataclass ──────────────────────────────────────────────────────────

@dataclass
class Peer:
    """One remote device that is currently online on the LAN."""

    device_id: str
    name:      str
    address:   str         # IPv4 address discovered from the UDP packet
    tcp_port:  int
    last_seen: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        """
        Stable identity inside this process.

        Why not just device_id?
        - On same-machine testing, two app instances share the same persisted
          device_id file, but listen on different TCP ports.
        - Using (device_id + tcp_port + address) lets us represent both.
        """
        return f"{self.device_id}@{self.address}:{self.tcp_port}"


# ── Combined Discovery service: beacon + listener + peer dict ───────────────

class Discovery:
    """
    Runs three things in background daemon threads:
      1. Beacon  — broadcasts a HELLO every BEACON_INTERVAL seconds.
      2. Listener — joins the multicast group and receives peers' HELLOs.
      3. Sweeper — drops peers we haven't heard from in PEER_TTL seconds.

    The UI calls ``start()`` once and reads ``peers`` whenever the
    ``on_change`` callback fires.
    """

    def __init__(self, device: Device, tcp_port: int,
                 on_change: Callable[[list[Peer]], None] = lambda _ps: None,
                 udp_port: int = UDP_PORT,
                 multicast_group: str = MULTICAST_GROUP):
        self.device          = device
        self.tcp_port        = tcp_port
        self.udp_port        = udp_port
        self.multicast_group = multicast_group
        self.on_change       = on_change

        self.peers: dict[str, Peer] = {}
        self._lock      = threading.Lock()
        self._stop      = threading.Event()
        self._send_sock: socket.socket | None = None
        self._recv_sock: socket.socket | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        self._send_sock = self._make_sender()
        self._recv_sock = self._make_listener()
        threading.Thread(target=self._beacon_loop,   daemon=True, name="beacon").start()
        threading.Thread(target=self._listener_loop, daemon=True, name="listener").start()
        threading.Thread(target=self._sweeper_loop,  daemon=True, name="sweeper").start()

    def stop(self) -> None:
        self._stop.set()
        try:
            packet = protocol.encode_udp(
                protocol.BYE,
                device_id=self.device.device_id,
                tcp_port=self.tcp_port,
            )
            self._send_sock.sendto(packet, (self.multicast_group, self.udp_port))
            try:
                self._send_sock.sendto(packet, ("255.255.255.255", self.udp_port))
            except OSError:
                pass
        except (OSError, AttributeError):
            pass
        for s in (self._send_sock, self._recv_sock):
            try:
                if s: s.close()
            except OSError:
                pass

    # ── Socket setup ────────────────────────────────────────────────────────

    def _make_sender(self) -> socket.socket:
        """Create a UDP socket configured to send multicast packets."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                     struct.pack("B", MULTICAST_TTL))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP,
                     struct.pack("B", 1))   # so two processes on one PC see each other
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)  # fallback
        except OSError:
            pass
        return s

    def _make_listener(self) -> socket.socket:
        """Create a UDP socket bound to udp_port and joined to the multicast group."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s.bind(("", self.udp_port))
        s.settimeout(1.0)

        mreq = struct.pack("4sl", socket.inet_aton(self.multicast_group), socket.INADDR_ANY)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass
        return s

    # ── Background loops ────────────────────────────────────────────────────

    def _beacon_loop(self) -> None:
        """Send a HELLO every BEACON_INTERVAL seconds until stopped."""
        while not self._stop.is_set():
            packet = protocol.encode_udp(
                protocol.HELLO,
                device_id=self.device.device_id,
                name=self.device.name,
                tcp_port=self.tcp_port,
                version=PROTOCOL_VERSION,
            )
            try:
                self._send_sock.sendto(packet, (self.multicast_group, self.udp_port))
            except OSError:
                pass
            # Broadcast fallback for LANs where multicast is filtered.
            try:
                self._send_sock.sendto(packet, ("255.255.255.255", self.udp_port))
            except OSError:
                pass
            self._stop.wait(BEACON_INTERVAL)

    def _listener_loop(self) -> None:
        """Receive HELLO/BYE packets and update the peer dict."""
        while not self._stop.is_set():
            try:
                data, addr = self._recv_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            msg = protocol.decode_udp(data)
            if not msg:
                continue
            device_id = msg.get("device_id")
            if not device_id:
                continue

            # Ignore only our *exact own* beacon. Same-machine testing may
            # run multiple instances sharing the same persisted device_id, so
            # we must NOT drop packets from same device_id on a different port.
            try:
                msg_tcp_port = int(msg.get("tcp_port", -1))
            except (TypeError, ValueError):
                msg_tcp_port = -1
            if device_id == self.device.device_id and msg_tcp_port == self.tcp_port:
                continue

            if msg.get("type") == protocol.HELLO:
                self._upsert_peer(msg, addr[0])
            elif msg.get("type") == protocol.BYE:
                self._remove_peer(msg, addr[0])

    def _sweeper_loop(self) -> None:
        """Periodically drop peers we haven't heard from in PEER_TTL seconds."""
        while not self._stop.wait(2.0):
            cutoff = time.time() - PEER_TTL
            changed = False
            with self._lock:
                stale = [k for k, p in self.peers.items() if p.last_seen < cutoff]
                for k in stale:
                    del self.peers[k]
                    changed = True
            if changed:
                self._notify()

    # ── Peer dict mutation ──────────────────────────────────────────────────

    def _upsert_peer(self, msg: dict, address: str) -> None:
        try:
            peer = Peer(
                device_id=msg["device_id"],
                name=msg.get("name", "Unknown"),
                address=address,
                tcp_port=int(msg["tcp_port"]),
                last_seen=time.time(),
            )
        except (KeyError, TypeError, ValueError):
            return
        with self._lock:
            new = peer.key not in self.peers
            self.peers[peer.key] = peer
        if new:
            self._notify()

    def _remove_peer(self, msg: dict, address: str) -> None:
        device_id = msg.get("device_id")
        if not device_id:
            return
        tcp_port = msg.get("tcp_port")
        key = None
        if tcp_port is not None:
            try:
                key = f"{device_id}@{address}:{int(tcp_port)}"
            except (TypeError, ValueError):
                key = None

        with self._lock:
            if key:
                existed = self.peers.pop(key, None) is not None
            else:
                # Backward compatibility for BYE packets without tcp_port:
                # remove all peers with this device_id.
                remove_keys = [k for k, p in self.peers.items() if p.device_id == device_id]
                existed = bool(remove_keys)
                for k in remove_keys:
                    self.peers.pop(k, None)
        if existed:
            self._notify()

    def snapshot(self) -> list[Peer]:
        with self._lock:
            return sorted(self.peers.values(), key=lambda p: p.name.lower())

    def _notify(self) -> None:
        try:
            self.on_change(self.snapshot())
        except Exception:
            pass
