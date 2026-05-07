# Networks Course Discussion Guide

This guide is written for your viva/discussion so the networking terms are explicit and easy to explain.

---

## 1) What this project is (one-line answer)

A **peer-to-peer LAN file sharing app** where:
- devices discover each other using **UDP multicast**,
- files are transferred using **TCP sockets**,
- integrity is verified with **SHA-256 end-to-end hashing**.

---

## 2) Networking terms you should say clearly

## TCP vs UDP in this project

- **UDP (connectionless)** is used only for **discovery**.
  - No handshake, just periodic HELLO datagrams.
  - Message loss is acceptable because another HELLO is sent every 3 seconds.
- **TCP (connection-oriented)** is used for **file transfer**.
  - Reliable, ordered delivery, retransmission, flow control.
  - Needed for large file transfer correctness.

## Multicast

- Discovery uses multicast group `239.42.42.42` on UDP port `5002`.
- TTL = `1`, so packets stay on the local LAN.
- Why multicast: better behavior than broadcast for multiple local processes on Windows.

## Application-layer protocol

On top of TCP, the app defines custom messages:
- `REQUEST` -> ask receiver permission + send file manifest
- `RESPONSE` -> accept/reject
- `METADATA` -> filename/size/hash
- `DATA` -> binary chunk
- `ACK` -> chunk/file acknowledgment
- `DONE` -> end of one file
- `SESSION_DONE` -> end of all files in current session

Framing format:
- 4-byte big-endian header length
- JSON header
- optional binary payload for `DATA`

## Integrity

- Sender computes SHA-256 before sending.
- Receiver recomputes SHA-256 after writing to disk.
- If mismatch -> transfer is rejected as corrupted.

---

## 3) How to run the project

From project root (`d:\projects\File_transfer_app`):

```powershell
python main.py
```

Optional:

```powershell
python main.py --name "My-Laptop"
python main.py --save-dir "D:\Received"
```

---

## 4) Correctness checklist (what to verify)

When you run two instances, verify these in order:

1. **Discovery works**
   - Both devices appear in the left "DEVICES ON LAN" list within ~3-10 seconds.
2. **Handshake works**
   - Sender clicks Send.
   - Receiver gets Accept/Reject popup.
3. **Transfer works**
   - Progress bar updates.
   - Log shows chunk acknowledgments and completion.
4. **Integrity works**
   - Receiver log shows successful save.
   - File opens correctly and size matches source.

---

## 5) Running tests (recommended before discussion)

```powershell
python -m unittest discover -s tests -v
```

Expected result:
- all tests pass (`OK`)

What tests cover:
- protocol framing correctness
- discovery behavior
- single/multi-file transfer
- rejection/cancellation/error paths

---

## 6) Same-device test (two terminals on one PC)

Yes, it is possible.

Use this (different TCP ports, same UDP discovery port):

```powershell
# Terminal 1
python main.py --name "Device-A" --tcp-port 5001

# Terminal 2
python main.py --name "Device-B" --tcp-port 5003
```

If discovery still fails on same machine, use this troubleshooting flow.

---

## 7) Troubleshooting when two instances cannot discover each other

## Step A: make sure no old process is still using ports

```powershell
Get-NetTCPConnection -LocalPort 5001 -ErrorAction SilentlyContinue
Get-NetUDPEndpoint -LocalPort 5002 -ErrorAction SilentlyContinue
```

If many stale Python processes appear, close them or end them.

## Step B: allow Python in Windows Firewall (Private network)

- Open **Windows Defender Firewall -> Allow an app**
- Ensure **python.exe** is allowed for **Private** network
- Restart both app instances

If UDP 5002 is blocked on your machine, run both instances with a different
shared discovery port:

```powershell
# Terminal 1
python main.py --name "Device-A" --tcp-port 5001 --udp-port 55002

# Terminal 2
python main.py --name "Device-B" --tcp-port 5003 --udp-port 55002
```

## Step C: verify same network profile

If testing on two different computers:
- both must be on same Wi-Fi / same subnet
- AP isolation on router must be OFF

## Step D: wait enough time

- Beacon interval is 3s, peer TTL is 10s
- Give it at least 10 seconds initially

---

## 8) Fast demo plan for your doctor/professor

1. Run tests: `python -m unittest discover -s tests -v`
2. Start Device-A and Device-B (same PC or two PCs)
3. Show discovery list populating
4. Send file from A to B
5. Show Accept popup on B
6. Show progress + completion logs
7. Mention SHA-256 verification step

---

## 9) 30-second oral summary (memorize this)

"This app is a LAN P2P file sharing system. It uses UDP multicast for device discovery because discovery is connectionless and tolerant to packet loss. Once a peer is selected, it switches to TCP for reliable ordered file transfer. On top of TCP, I implemented a custom application-layer protocol with explicit framing and message types like REQUEST, DATA, and ACK. For integrity, I verify SHA-256 after file reconstruction on the receiver side."
