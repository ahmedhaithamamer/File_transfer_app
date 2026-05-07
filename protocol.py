import json
import struct

# ── TCP message types ───────────────────────────────────────────────────────
REQUEST   = "REQUEST"     # sender → receiver: manifest of files; awaits accept
RESPONSE  = "RESPONSE"    # receiver → sender: {accepted: bool, reason: str}
METADATA  = "METADATA"    # sender → receiver: filename, size, sha256
DATA      = "DATA"        # sender → receiver: one chunk of file bytes
ACK       = "ACK"         # receiver → sender: per-chunk / per-file ack
DONE      = "DONE"        # sender → receiver: end of one file
SESSION_DONE = "SESSION_DONE"  # sender → receiver: end of the whole session
CANCEL    = "CANCEL"      # either → either: abort
ERROR     = "ERROR"       # either → either: fatal error

_HEADER_LEN_FMT  = ">I"                     # 4-byte big-endian unsigned int
_HEADER_LEN_SIZE = struct.calcsize(_HEADER_LEN_FMT)


# ─────────────────────────────────────────────────────────────────────────────
# TCP framing
# ─────────────────────────────────────────────────────────────────────────────

def send_msg(sock, msg_type: str, *, payload: bytes = b"", **fields) -> None:
    """
    Frame and transmit one message over a TCP socket.

    Steps (mapping to socket calls):
      1. Build header dict {type, ...fields} and JSON-encode it.
      2. Send 4-byte header length prefix using ``sock.sendall``.
      3. Send the JSON header bytes.
      4. If a binary payload is given (DATA messages), send it after.
    """
    header = {"type": msg_type, **fields}
    header_bytes = json.dumps(header).encode("utf-8")

    sock.sendall(struct.pack(_HEADER_LEN_FMT, len(header_bytes)))   # length prefix
    sock.sendall(header_bytes)                                      # JSON header
    if payload:
        sock.sendall(payload)                                       # binary blob


def recv_msg(sock) -> dict:
    """
    Read exactly one message from a TCP socket and return it as a dict.

    For DATA messages the binary payload is placed under ``msg["payload"]``.
    Raises :class:`ConnectionError` if the peer closes mid-message.
    """
    raw_len = _recv_exact(sock, _HEADER_LEN_SIZE)
    (header_len,) = struct.unpack(_HEADER_LEN_FMT, raw_len)
    header = json.loads(_recv_exact(sock, header_len).decode("utf-8"))

    if header.get("type") == DATA:
        size = int(header.get("payload_size", 0))
        header["payload"] = _recv_exact(sock, size) if size else b""

    return header


def _recv_exact(sock, n: int) -> bytes:
    """
    Loop on ``sock.recv`` until exactly *n* bytes have been read.

    Why we need this:
      ``socket.recv(n)`` may return *fewer* than ``n`` bytes because TCP is
      a stream — the kernel hands us whatever has arrived so far.  Application
      protocols must always read in a loop until they have what they need.
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(
                f"Connection closed: expected {n} bytes, got {len(buf)}."
            )
        buf.extend(chunk)
    return bytes(buf)
