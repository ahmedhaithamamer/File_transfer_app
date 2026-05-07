"""
All configuration constants in one place.

Networking notes for the discussion:
    - TCP port 5001 carries reliable, ordered file data.
"""

# TCP — reliable file transfer
TCP_PORT          = 5001
CHUNK_SIZE        = 256 * 1024     # 256 KB per chunk (good for LAN)
SOCKET_BUFFER     = 1024 * 1024    # 1 MB SO_SNDBUF / SO_RCVBUF

# Timeouts (seconds)
CONNECT_TIMEOUT   = 10.0
TRANSFER_TIMEOUT  = 60.0
ACCEPT_TIMEOUT    = 1.0            # accept() poll interval (lets stop() work)

# Storage
import os
SAVE_DIR          = os.path.abspath("received_files")
DEVICE_FILE       = os.path.expanduser("~/.lan_share_device.json")
PEERS_FILE        = os.path.expanduser("~/.lan_share_peers.json")

# Protocol
PROTOCOL_VERSION  = 2
