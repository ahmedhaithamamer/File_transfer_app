# LAN Share — TCP File Transfer

A peer-to-peer file sharing application for the local network.  
Each device runs one process: it **listens** for incoming files over TCP, and can **send** files to any device whose IP address you know.  
File integrity is verified end-to-end with **SHA-256**.

> For a deep dive into the networking design, read **[NETWORKING.md](NETWORKING.md)**.

---

## Quick start

**On every device:**

```powershell
python main.py
```

Optional flags:
```powershell
python main.py --name "Ahmad-Laptop"
python main.py --tcp-port 5002          # if 5001 is taken
python main.py --save-dir D:\downloads
```

**How to connect:**

1. Both devices launch the app.
2. **Device A** finds its IP address:
   ```powershell
   ipconfig        # look for IPv4 Address, e.g. 192.168.1.10
   ```
3. **Device B** types that IP into the *IP Address* field, enters the port (`5001`), optionally a label, and clicks **Add Peer**.
4. Select the peer in the list, click **Browse Files…**, click **Send**.
5. Device A gets a popup → **Accept** → files arrive in `received_files/`.

---

## Test on one machine (two terminals)

```powershell
# Terminal 1  (receives on 5001)
python main.py --name "Device-A" --tcp-port 5001

# Terminal 2  (receives on 5002)
python main.py --name "Device-B" --tcp-port 5002
```

In Device-B's window: IP = `127.0.0.1`, Port = `5001`, click Add Peer.  
In Device-A's window: IP = `127.0.0.1`, Port = `5002`, click Add Peer.  
Send files either way.

---

## Project structure

7 flat Python files, **zero external dependencies** (Python 3.11+ stdlib only):

```
File_transfer_app/
├── protocol.py     # Wire format: 4-byte framing + JSON header + binary payload
├── transfer.py     # TCP: send_files()  +  serve_forever()
├── ui.py           # Tkinter app — peer entry, file picker, progress, log
├── utils.py        # SHA-256, chunking, formatting, persistent device ID
├── config.py       # All ports, sizes, timeouts in one place
├── main.py         # Entry point
├── tests/
│   └── test_app.py # Protocol + transfer tests
├── NETWORKING.md   # Network architecture for the course discussion
└── README.md
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Device A (sender)              Device B (receiver)      │
│                                                          │
│  user types B's IP:port         TCP server listening     │
│                                                          │
│  TCP connect ──────────────────────────────────────────► │
│              ◄──────── REQUEST accepted? ───────────────  │
│  send METADATA + DATA chunks ──────────────────────────► │
│              ◄──────────────── ACK per chunk ───────────  │
│  send DONE ────────────────────────────────────────────► │
│              ◄──────── ACK + SHA-256 verified ──────────  │
└─────────────────────────────────────────────────────────┘
         All traffic: TCP port 5001 (configurable)
```

---

## Application-layer protocol

Every TCP message is framed identically:

```
┌─────────────────────────────┐
│ 4 bytes: header_len (BE u32)│
├─────────────────────────────┤
│ header_len bytes: JSON      │  e.g. {"type":"DATA","payload_size":262144}
├─────────────────────────────┤
│ N bytes: binary payload     │  ← DATA messages only
└─────────────────────────────┘
```

### Message flow

| Step | Type | Direction | Content |
|------|------|-----------|---------|
| 1 | `REQUEST` | Sender → Receiver | file manifest, total size |
| 2 | `RESPONSE` | Receiver → Sender | `{accepted: true/false}` |
| 3 | `METADATA` | Sender → Receiver | filename, size, sha256, chunk_size |
| 4 | `DATA` | Sender → Receiver | one binary chunk |
| 5 | `ACK` | Receiver → Sender | per-chunk acknowledgement |
| 6 | `DONE` | Sender → Receiver | end of file |
| 7 | `ACK` | Receiver → Sender | SHA-256 verified OK |
| 8 | `SESSION_DONE` | Sender → Receiver | all files transferred |

Steps 3–7 repeat for every file in a multi-file send.

---

## Running the tests

```powershell
python -m unittest discover -s tests -v
```

Tests cover:
- TCP framing round-trips (METADATA, DATA, REQUEST)
- Single-file transfer, multi-file session, empty file, exact chunk boundary
- Receiver rejects transfer
- Sender cancels mid-transfer
- Connection refused handling

---

## Configuration (`config.py`)

| Constant | Default | Purpose |
|---|---|---|
| `TCP_PORT` | 5001 | TCP listener port |
| `CHUNK_SIZE` | 256 KB | Bytes per DATA message |
| `SOCKET_BUFFER` | 1 MB | TCP send/receive buffer |
| `SAVE_DIR` | `received_files/` | Where received files land |

---

## Troubleshooting

**Can't connect to the other device**
- Make sure both are on the **same Wi-Fi network**.
- Find the receiver's IP with `ipconfig` (Windows) or `ip a` (Linux/Mac).
- Allow Python through **Windows Firewall** on the TCP port (5001).
- If using a hotspot, ensure clients are not isolated from each other.

**Transfer fails mid-way**
- Check the log panel — SHA-256 mismatch or a `CANCEL` message will appear.
- Retry; TCP will reconnect cleanly.

**Port already in use**
- Change with `--tcp-port 5002` on one device; type the matching port when adding that peer.
