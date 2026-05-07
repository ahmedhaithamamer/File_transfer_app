"""
File transfer over TCP — both sides in one file.

NETWORKING NOTES (for the discussion)
=====================================

Why TCP, not UDP?
    File transfer requires:
      * **Reliability** — every byte must arrive, exactly once.
      * **Ordering** — chunk N must arrive before chunk N+1.
      * **Flow control** — fast sender, slow receiver must not overflow.
    TCP gives us all three for free; UDP would need us to reimplement them.

What our application layer adds on top of TCP:
    1. **Message framing** (length-prefixed JSON) — see protocol.py.
    2. **A handshake** (REQUEST → RESPONSE) so the receiver can accept/reject.
    3. **Per-chunk acknowledgements** for fine-grained progress.
    4. **End-to-end SHA-256 verification** — TCP detects packet corruption,
       but only over its 16-bit checksum.  SHA-256 catches everything,
       including disk errors after the data has left the socket.

The session timeline of one transfer:

    Client                                  Server
      │                                       │
      │── socket.connect() ─────────────────> │  (TCP three-way handshake)
      │── REQUEST  (file manifest) ─────────> │
      │                          <── RESPONSE │  (user accepts in UI)
      │── for each file:                      │
      │     METADATA ───────────────────────> │
      │                          <───── ACK ──│
      │     DATA chunk 0 ───────────────────> │
      │                          <───── ACK ──│
      │     DATA chunk 1 ───────────────────> │
      │                          <───── ACK ──│
      │     ...                               │
      │     DONE ────────────────────────────>│  (server verifies SHA-256)
      │                          <───── ACK ──│
      │── SESSION_DONE ────────────────────── >│
      │                          <───── ACK ──│
      │── socket.close() ────────────────────>│
"""

import os
import socket
import threading
import time
from typing import Callable

import protocol
from config import (
    ACCEPT_TIMEOUT,
    CHUNK_SIZE,
    CONNECT_TIMEOUT,
    SAVE_DIR,
    SOCKET_BUFFER,
    TCP_PORT,
    TRANSFER_TIMEOUT,
)
from utils import compute_sha256, format_size, format_speed, iter_chunks


# ── Helpers used by both sides ──────────────────────────────────────────────

def _build_manifest(filepaths: list[str]) -> list[dict]:
    """Turn a list of paths into the JSON-serialisable manifest sent in REQUEST."""
    return [
        {"name":   os.path.basename(p),
         "size":   os.path.getsize(p),
         "sha256": compute_sha256(p)}
        for p in filepaths if os.path.isfile(p)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# SENDER  (client side of one transfer)
# ─────────────────────────────────────────────────────────────────────────────

def send_files(
    host: str,
    port: int,
    filepaths: list[str],
    sender_name: str,
    sender_id:   str,
    sender_tcp_port: int | None = None,
    on_log:      Callable[[str], None]                 = lambda m: None,
    on_progress: Callable[[int, int, float], None]     = lambda d, t, s: None,
    cancel_flag: threading.Event | None                 = None,
) -> bool:
    """
    Connect to *host:port* and send *filepaths* in a single session.

    Returns ``True`` on success.  All progress and log events are reported
    via the optional callbacks so a CLI or GUI can render them.
    """
    cancel_flag = cancel_flag or threading.Event()
    files = [p for p in filepaths if os.path.isfile(p)]
    if not files:
        on_log("No valid files to send."); return False

    # ── Step 1: hash the manifest ──────────────────────────────────────────
    on_log(f"Hashing {len(files)} file(s) …")
    manifest = _build_manifest(files)
    total    = sum(m["size"] for m in manifest)

    # ── Step 2: open TCP socket and connect ────────────────────────────────
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER)
    except OSError:
        pass

    try:
        on_log(f"Connecting to {host}:{port} …")
        sock.connect((host, port))                # ← TCP three-way handshake
        sock.settimeout(TRANSFER_TIMEOUT)
        on_log("Connected.")

        # ── Step 3: send REQUEST and wait for RESPONSE ─────────────────────
        protocol.send_msg(
            sock, protocol.REQUEST,
            sender_name=sender_name, sender_id=sender_id,
            sender_tcp_port=sender_tcp_port,
            manifest=manifest, total_size=total, file_count=len(manifest),
        )
        on_log(f"Awaiting receiver to accept ({format_size(total)}) …")
        resp = protocol.recv_msg(sock)
        if not resp.get("accepted"):
            on_log(f"Receiver rejected: {resp.get('reason', 'rejected')}")
            return False
        on_log("Accepted. Uploading …")

        # ── Step 4: for each file, send METADATA + DATA + DONE ─────────────
        sent_total = 0
        start = time.monotonic()
        for entry, path in zip(manifest, files):
            if cancel_flag.is_set():
                protocol.send_msg(sock, protocol.CANCEL, message="cancelled")
                on_log("Cancelled."); return False

            protocol.send_msg(
                sock, protocol.METADATA,
                filename=entry["name"], filesize=entry["size"],
                sha256=entry["sha256"], chunk_size=CHUNK_SIZE,
            )
            ack = protocol.recv_msg(sock)
            if ack.get("status") != "ok":
                on_log(f"Server rejected metadata: {ack.get('message')}"); return False

            for chunk in iter_chunks(path):
                if cancel_flag.is_set():
                    protocol.send_msg(sock, protocol.CANCEL, message="cancelled")
                    on_log("Cancelled."); return False

                protocol.send_msg(
                    sock, protocol.DATA,
                    payload_size=len(chunk),
                    payload=chunk,
                )
                ack = protocol.recv_msg(sock)
                if ack.get("status") != "ok":
                    on_log(f"Server error: {ack.get('message')}"); return False

                sent_total += len(chunk)
                elapsed = time.monotonic() - start
                speed   = sent_total / elapsed if elapsed > 0 else 0.0
                on_progress(sent_total, total, speed)

            protocol.send_msg(sock, protocol.DONE)
            ack = protocol.recv_msg(sock)
            if ack.get("status") != "ok":
                on_log(f"Server checksum failed: {ack.get('message')}"); return False
            on_log(f"Sent: {entry['name']} ({format_size(entry['size'])})")

        # ── Step 5: SESSION_DONE ───────────────────────────────────────────
        protocol.send_msg(sock, protocol.SESSION_DONE)
        protocol.recv_msg(sock)  # final session-level ACK

        elapsed = time.monotonic() - start
        avg = sent_total / elapsed if elapsed > 0 else 0
        on_log(f"All done. {format_size(sent_total)} in {elapsed:.1f}s "
               f"(avg {format_speed(avg)})")
        return True

    except ConnectionRefusedError:
        on_log(f"Connection refused — is the receiver running?"); return False
    except socket.timeout:
        on_log("Timeout."); return False
    except Exception as exc:
        on_log(f"Error: {exc}"); return False
    finally:
        try: sock.close()
        except OSError: pass


# ─────────────────────────────────────────────────────────────────────────────
# RECEIVER  (server side — listening for connections)
# ─────────────────────────────────────────────────────────────────────────────

def serve_forever(
    port: int,
    accept_callback: Callable[[dict], bool],
    on_log:          Callable[[str], None]              = lambda m: None,
    on_progress:     Callable[[int, int, float], None]  = lambda d, t, s: None,
    on_file_saved:   Callable[[str], None]              = lambda p: None,
    save_dir: str = SAVE_DIR,
    stop_flag: threading.Event | None = None,
) -> None:
    """
    Bind a TCP socket on *port* and serve incoming transfers forever.

    Each accepted connection is handed to ``_handle_connection`` running in
    its own daemon thread so the server can accept multiple senders at once.
    """
    stop_flag = stop_flag or threading.Event()
    os.makedirs(save_dir, exist_ok=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER)
    except OSError:
        pass
    server.bind(("", port))                          # ← INADDR_ANY: all interfaces
    server.listen(8)                                 # ← queue up to 8 pending connections
    server.settimeout(ACCEPT_TIMEOUT)

    on_log(f"TCP server listening on port {port} (saving to {save_dir})")

    while not stop_flag.is_set():
        try:
            conn, addr = server.accept()             # ← blocking until a client connects
        except socket.timeout:
            continue
        except OSError:
            break

        on_log(f"Connection from {addr[0]}:{addr[1]}")
        threading.Thread(
            target=_handle_connection,
            args=(conn, addr, save_dir, accept_callback, on_log, on_progress, on_file_saved),
            daemon=True,
        ).start()

    server.close()


def _handle_connection(
    conn:            socket.socket,
    addr:            tuple,
    save_dir:        str,
    accept_callback: Callable[[dict], bool],
    on_log:          Callable[[str], None],
    on_progress:     Callable[[int, int, float], None],
    on_file_saved:   Callable[[str], None],
) -> None:
    """Drive one accepted connection through its full life-cycle."""
    conn.settimeout(TRANSFER_TIMEOUT)
    try:
        # ── Step 1: read REQUEST ───────────────────────────────────────────
        msg = protocol.recv_msg(conn)
        if msg.get("type") != protocol.REQUEST:
            protocol.send_msg(conn, protocol.ERROR,
                              message=f"Expected REQUEST, got {msg.get('type')}")
            return

        manifest = msg.get("manifest", [])
        total    = int(msg.get("total_size", 0))
        msg["sender_ip"] = addr[0]
        on_log(f"Request from {msg.get('sender_name','?')}: "
               f"{len(manifest)} file(s), {format_size(total)}")

        # ── Step 2: ask the UI to accept/reject ────────────────────────────
        accepted = bool(accept_callback(msg))
        protocol.send_msg(
            conn, protocol.RESPONSE,
            accepted=accepted,
            reason="" if accepted else "Rejected by user.",
        )
        if not accepted:
            on_log("Rejected."); return

        on_log("Accepted, receiving …")

        # ── Step 3: receive each file ──────────────────────────────────────
        received_total = 0
        start = time.monotonic()

        while True:
            msg = protocol.recv_msg(conn)
            mtype = msg.get("type")

            if mtype == protocol.METADATA:
                received_total += _receive_one_file(
                    conn, msg, save_dir, total,
                    received_total, start, on_progress, on_log, on_file_saved,
                )

            elif mtype == protocol.SESSION_DONE:
                protocol.send_msg(conn, protocol.ACK, status="ok",
                                  message="Session complete")
                on_log("Session complete."); return

            elif mtype == protocol.CANCEL:
                on_log(f"Sender cancelled: {msg.get('message','')}"); return

            else:
                protocol.send_msg(conn, protocol.ERROR,
                                  message=f"Unexpected message: {mtype}")
                return

    except ConnectionError as exc:
        on_log(f"Connection lost: {exc}")
    except Exception as exc:
        on_log(f"Error: {exc}")
    finally:
        try: conn.close()
        except OSError: pass


def _receive_one_file(
    conn:           socket.socket,
    metadata:       dict,
    save_dir:       str,
    session_total:  int,
    bytes_so_far:   int,
    session_start:  float,
    on_progress:    Callable[[int, int, float], None],
    on_log:         Callable[[str], None],
    on_file_saved:  Callable[[str], None],
) -> int:
    """
    Read DATA chunks until DONE, write them to disk, then verify SHA-256.
    Returns the number of bytes written for this file.
    """
    filename       = os.path.basename(metadata["filename"])
    filesize       = int(metadata["filesize"])
    expected_hash  = metadata.get("sha256", "")
    save_path      = _resolve_save_path(save_dir, filename)

    on_log(f"Receiving '{filename}' ({format_size(filesize)})")
    protocol.send_msg(conn, protocol.ACK, status="ok", message="Metadata accepted")

    written = 0
    with open(save_path, "wb") as f:
        while True:
            msg   = protocol.recv_msg(conn)
            mtype = msg.get("type")

            if mtype == protocol.DATA:
                payload = msg.get("payload", b"")
                f.write(payload)
                written += len(payload)
                elapsed = time.monotonic() - session_start
                speed   = (bytes_so_far + written) / elapsed if elapsed > 0 else 0
                on_progress(bytes_so_far + written, session_total, speed)
                protocol.send_msg(conn, protocol.ACK, status="ok", message="Chunk OK")

            elif mtype == protocol.DONE:
                break

            elif mtype == protocol.CANCEL:
                raise ConnectionError(f"Sender cancelled: {msg.get('message','')}")

            else:
                raise ValueError(f"Unexpected message inside file: {mtype}")

    # End-to-end SHA-256 verification.
    if expected_hash:
        actual = compute_sha256(save_path)
        if actual != expected_hash:
            os.remove(save_path)
            detail = f"Checksum mismatch (expected …{expected_hash[-12:]}, got …{actual[-12:]})"
            protocol.send_msg(conn, protocol.ACK, status="checksum_error", message=detail)
            raise ValueError(detail)

    protocol.send_msg(conn, protocol.ACK, status="ok", message="File complete")
    on_log(f"Saved: {save_path}")
    on_file_saved(save_path)
    return written


def _resolve_save_path(save_dir: str, filename: str) -> str:
    """Avoid overwriting existing files by appending a numeric suffix."""
    candidate = os.path.join(save_dir, filename)
    if not os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(filename)
    n = 1
    while True:
        candidate = os.path.join(save_dir, f"{base} ({n}){ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1
