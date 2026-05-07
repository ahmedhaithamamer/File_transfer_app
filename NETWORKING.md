# Networking Architecture — LAN Share (TCP-Only)

This document explains the final system from a networking perspective. The
current implementation uses only TCP for transfer; peer selection is manual
(IP + port) in the UI.

---

## 1. The OSI / TCP-IP layers used

```
Application layer: custom framed protocol (this project)
Transport layer:   TCP
Network layer:     IP
Link layer:        Ethernet / Wi-Fi (handled by OS)
```

---

## 2. Why TCP

File transfer requires:
- reliability (every byte arrives),
- ordering (chunk N before N+1),
- flow control (sender does not overflow receiver).

TCP provides these natively, so we avoid reimplementing transport logic.

---

## 3. File transfer flow

### Why we still need application-layer logic on top of TCP

TCP gives us:
- **Reliability** — retransmission, error correction at packet level
- **Ordering** — bytes arrive in order
- **Flow control** — sender slows down when receiver is full
- **Congestion control** — backs off when network is busy

TCP does **not** give us:
- Message boundaries — TCP is a **byte stream**, not a message stream
- Application-level structure — what does each byte mean?
- End-to-end integrity — TCP's checksum is only 16 bits per segment, can be defeated by a hash collision or corruption after the kernel hands data over

So our application layer adds:

1. **Message framing** ([protocol.py](protocol.py)) — `4-byte length` + `JSON header` + optional `binary payload`
2. **Typed messages** — `REQUEST`, `RESPONSE`, `METADATA`, `DATA`, `ACK`, `DONE`, …
3. **A handshake** — `REQUEST`/`RESPONSE` so the user can accept or reject
4. **Per-chunk ACKs** — fine-grained progress + early failure detection
5. **End-to-end SHA-256** — catches every kind of corruption, including disk errors

### The full session timeline

```mermaid
sequenceDiagram
    participant S as Sender
    participant R as Receiver

    Note over S,R: TCP three-way handshake
    S->>R: SYN
    R->>S: SYN+ACK
    S->>R: ACK

    Note over S,R: Application protocol begins
    S->>R: REQUEST { manifest:[...], total_size }
    Note right of R: UI shows accept dialog
    R->>S: RESPONSE { accepted: true }

    loop for each file
        S->>R: METADATA { filename, size, sha256 }
        R->>S: ACK ok
        loop for each 256 KB chunk
            S->>R: DATA { payload_size + raw bytes }
            R->>S: ACK ok
        end
        S->>R: DONE
        Note right of R: Verify SHA-256
        R->>S: ACK ok
    end

    S->>R: SESSION_DONE
    R->>S: ACK ok

    Note over S,R: TCP four-way close
    S->>R: FIN
    R->>S: FIN+ACK
```

### Key socket calls

**Sender** ([transfer.py](transfer.py) `send_files`):
```python
sock = socket.socket(AF_INET, SOCK_STREAM)         # TCP socket
sock.connect((host, port))                          # ← TCP three-way handshake
send_msg(sock, REQUEST, manifest=..., ...)          # send JSON-framed message
resp = recv_msg(sock)                               # block until RESPONSE arrives
for chunk in iter_chunks(file):
    send_msg(sock, DATA, payload=chunk, ...)        # send one DATA message
    ack = recv_msg(sock)                            # wait for ACK
sock.close()                                        # ← FIN, TCP four-way close
```

**Receiver** ([transfer.py](transfer.py) `serve_forever`):
```python
server = socket.socket(AF_INET, SOCK_STREAM)
server.bind(("", 5001))                             # INADDR_ANY: all interfaces
server.listen(8)                                    # backlog: queued half-open conns
while True:
    conn, addr = server.accept()                     # ← block until client connects
    threading.Thread(target=handle, args=(conn,)).start()
```

`bind("", 5001)` uses `INADDR_ANY` (`0.0.0.0`) so we accept connections on **any** local interface (Wi-Fi, Ethernet, loopback). `listen(8)` sets the kernel's accept backlog — up to 8 pending connections can wait while we're busy.

### Why per-chunk ACK?

Without per-chunk ACK, the sender would happily push 100 MB into the kernel's TCP send buffer, then think it's "done" — even if the receiver crashed after byte 1. Per-chunk ACK is round-trip-bound, so the sender knows after each 256 KB whether the receiver is still alive and writing to disk successfully. It also gives smooth UI progress (1 chunk = 1 progress update).

The trade-off: **throughput drops**. A single round-trip-time (RTT) per chunk caps speed. On a 1ms-RTT LAN with 256 KB chunks, the upper bound is ~250 MB/s — fine for our use case. On a 50ms-RTT WAN it would be ~5 MB/s, which is why production protocols (HTTP/2, gRPC, BitTorrent) **pipeline** multiple chunks before waiting for ACKs.

### Why end-to-end SHA-256?

Because **TCP's 16-bit checksum can be defeated**. Some real failure modes:
- A faulty NIC corrupts bytes after TCP checksum verification
- The kernel writes correctly to disk, but the disk has a bad sector
- Memory corruption between socket buffer and `f.write()`

SHA-256 is computed by the **sender** before transmission and verified by the **receiver** after writing to disk. If a single bit differs anywhere along the entire path, the digest changes and we delete the corrupted file. This is the "end-to-end argument in system design" (Saltzer, Reed, Clark — 1984).

---

## 4. Concurrency model

The app uses **OS threads**, not async I/O, because socket programming with threads is the textbook model and easier to reason about for a course discussion.

```mermaid
flowchart TB
    subgraph MainProcess [LAN Share Process]
        UIThread["Main thread<br/>(Tkinter UI)"]
        AcceptT["TCP accept thread<br/>server.accept()"]
        RecvT1["receiver handler 1<br/>(spawned per connection)"]
        RecvT2["receiver handler 2<br/>(spawned per connection)"]
        SendT["sender thread<br/>(spawned per outgoing transfer)"]
    end

    UIThread -.->|"queue.Queue"| AcceptT
    AcceptT --> RecvT1
    AcceptT --> RecvT2
    UIThread --> SendT
```

### Thread safety

Tkinter is **not** thread-safe — only the main thread may touch widgets. Every other thread sends events to the UI through `queue.Queue`. The UI calls `self.after(50, self._poll_queue)` to drain the queue from the main thread on a 50 ms timer.

---

## 5. Reliability summary

| Failure mode | What catches it |
|---|---|
| Single bit flip in a TCP segment | TCP checksum (16-bit) |
| Lost TCP segment | TCP retransmission |
| Out-of-order TCP segments | TCP reassembly buffer |
| Connection drop mid-transfer | `recv()` returns 0 → `ConnectionError` raised → UI shows error |
| Receiver disk full / file write fails | Exception in `_handle_connection` → ERROR sent → sender aborts |
| Bit corruption *outside* the TCP stack (NIC, RAM, disk) | **Application-level SHA-256** at end of each file |
| Receiver rejects the transfer | `RESPONSE { accepted: false }` |
| Sender or receiver crashes | TCP RST → other side gets `ConnectionResetError` |

---

## 6. What you can demonstrate live

| Question the prof might ask | Demo / answer |
|---|---|
| "Show me the TCP three-way handshake" | Wireshark filter: `tcp.port == 5001` while clicking Send. Three packets: SYN, SYN+ACK, ACK |
| "Show me the application-layer messages" | Wireshark "Follow TCP Stream" — you can see the JSON headers in cleartext |
| "What if you remove SHA-256?" | Comment out the verification block → flip a bit in a received file → silently corrupted, sender thinks it succeeded |
| "How do peers find each other?" | In this final version, user enters IP and port manually for predictable behavior. |
| "What's the security model?" | Receiver shows a dialog with the file list before any DATA is sent. Currently no encryption — could add TLS via `ssl.wrap_socket()` in ~5 lines |

---

## 7. Suggested talking points

1. **"We chose TCP for transfer because we need ordering, reliability, and flow control — these are exactly what TCP provides for free, and reimplementing them on UDP would mean writing a worse TCP."**

2. **"We removed automatic discovery from the final version and use manual IP + port selection for consistent behavior across networks and easier demos."**

3. **"TCP gives us a byte stream, not a message stream. Our framing layer (4-byte length prefix + JSON header) recovers message boundaries — without it, `recv()` would return arbitrary fragments."**

4. **"Per-chunk ACK gives us application-layer flow visibility. TCP's flow control happens between kernels; our ACK happens between processes, so we know the receiver actually wrote the chunk to disk."**

5. **"End-to-end integrity needs end-to-end checks. TCP's 16-bit checksum protects only the network path. SHA-256 over the whole file protects against everything — including bugs in our own code."**

6. **"Future enhancement could add authenticated device discovery, but keeping TCP-only flow makes the course discussion cleaner and more robust."**
