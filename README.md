# LAN Share

Reliable peer-to-peer file transfer over a local network using Python sockets and Tkinter.

Each device runs one app instance that:
- listens for incoming files on TCP,
- sends files to known peers (manual IP + port),
- verifies every received file with SHA-256,
- keeps a persistent peer list for quick reuse.

This repository is designed for academic networking discussions and practical demos.

> Prefer a no-setup run? Use the packaged executable at `dist/LANShare/LANShare.exe` directly (no Python installation required).

## Highlights

- **TCP-only transfer flow** for predictable behavior across networks.
- **Custom application-layer protocol** with explicit message framing.
- **Multi-file transfer** in one session.
- **Receiver approval dialog** (Accept/Reject) before transfer starts.
- **Per-chunk ACKs** with live progress and speed.
- **End-to-end SHA-256 integrity check** after each file.
- **Persistent peers** in local JSON storage.
- **Peer management UX**: add, auto-save on accepted incoming transfer, right-click edit/delete.
- **Open received folder** directly from the UI.
- **No third-party runtime dependencies** (Python standard library only).

## Demo Snapshot

Sender connects to receiver over TCP and exchanges:
`REQUEST -> RESPONSE -> METADATA -> DATA/ACK ... -> DONE/ACK -> SESSION_DONE/ACK`

All traffic runs on a configurable TCP port (default: `5001`).

## Repository Structure

```text
File_transfer_app/
|- main.py                 # App entry point and CLI flags
|- ui.py                   # Tkinter interface and user actions
|- transfer.py             # TCP sender/receiver logic
|- protocol.py             # Framing and protocol message helpers
|- utils.py                # Hashing, chunk iteration, formatting, identity helpers
|- config.py               # Config constants (ports, buffers, timeouts, paths)
|- tests/test_app.py       # Automated tests
|- NETWORKING.md           # Networking-focused explanation
|- documentaion/report.tex # LaTeX project report
`- README.md
```

## Requirements

- Python **3.11+**
- OS: Windows / Linux / macOS (tested primarily on Windows)

Install dependencies:

```powershell
pip install -r requirements.txt
```

Note: runtime uses standard library only.

## Run the App

```powershell
python main.py
```

Optional flags:

```powershell
python main.py --name "My-Laptop"
python main.py --tcp-port 5002
python main.py --save-dir D:\downloads
```

## How to Use (Two Devices)

1. Launch app on both devices.
2. On receiver device, find IPv4:
   - Windows: `ipconfig`
   - Linux/macOS: `ip a` or `ifconfig`
3. On sender app, add peer using receiver IP + receiver TCP port.
4. Select peer, choose files, click **Send**.
5. Receiver clicks **Accept**.
6. Files are saved to `received_files/` (or your custom `--save-dir`).

## Same-Machine Test

Run two instances on different TCP ports:

```powershell
# Terminal 1
python main.py --name "Device-A" --tcp-port 5001

# Terminal 2
python main.py --name "Device-B" --tcp-port 5002
```

Then:
- In Device-A, add peer `127.0.0.1:5002`
- In Device-B, add peer `127.0.0.1:5001`

## Protocol Overview

TCP stream framing:

1. `4-byte big-endian header length`
2. `JSON header`
3. `optional binary payload` (for `DATA`)

Core message types:
- `REQUEST`, `RESPONSE`
- `METADATA`, `DATA`, `ACK`, `DONE`
- `SESSION_DONE`
- `CANCEL`, `ERROR`

See `protocol.py` and `transfer.py` for implementation details.

## Testing

Run all tests:

```powershell
python -m unittest discover -s tests -v
```

Coverage includes:
- protocol framing round-trips,
- single and multi-file transfer,
- empty file and chunk-boundary behavior,
- rejection, cancellation, and connection-refused handling.

## Packaging (Optional)

To build executable (if needed for distribution), use PyInstaller.
Spec files are not required; PyInstaller can regenerate them.

If a packaged build is available in this repository, run:

`dist/LANShare/LANShare.exe`

No Python setup is required for that executable.

## Configuration

Main values in `config.py`:
- `TCP_PORT` (default `5001`)
- `CHUNK_SIZE` (`256 * 1024`)
- `SOCKET_BUFFER` (`1024 * 1024`)
- `CONNECT_TIMEOUT`, `TRANSFER_TIMEOUT`, `ACCEPT_TIMEOUT`
- `SAVE_DIR`, `DEVICE_FILE`, `PEERS_FILE`

## Documentation

- Networking explanation: `NETWORKING.md`
- Submission report (LaTeX): `documentaion/report.tex`

## Troubleshooting

**Cannot connect to other device**
- Ensure both devices are on the same LAN.
- Verify receiver IP and TCP port are correct.
- Allow Python through firewall for selected TCP port.

**Port already in use**
- Start app with another port, e.g. `--tcp-port 5002`.
- Add peer with the same port you configured.

**Transfer interrupted or failed**
- Check in-app log for timeout, reject, cancel, or checksum messages.
- Retry transfer after verifying connectivity.

## Project Context

This project was developed for **ECE338: Computer Networks** as a practical implementation of socket programming, transport reliability, and application-layer protocol design.
