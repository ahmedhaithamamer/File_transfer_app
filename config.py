"""
All configuration constants in one place.

Networking notes for the discussion:
    - TCP port 5001 carries reliable, ordered file data.
    - UDP port 55002 carries unreliable broadcast/multicast HELLO beacons.
    - Multicast group 239.42.42.42 is in the locally-scoped admin range
      (239.0.0.0/8) and travels only on the local LAN (TTL=1).
"""

# TCP — reliable file transfer
TCP_PORT          = 5001
CHUNK_SIZE        = 256 * 1024     # 256 KB per chunk (good for LAN)
SOCKET_BUFFER     = 1024 * 1024    # 1 MB SO_SNDBUF / SO_RCVBUF

# UDP — discovery (multicast)
UDP_PORT          = 55002
MULTICAST_GROUP   = "239.42.42.42"
MULTICAST_TTL     = 1              # 1 = local LAN only
BEACON_INTERVAL   = 3.0            # seconds between HELLO broadcasts
PEER_TTL          = 10.0           # peers expire after this many seconds of silence

# Timeouts (seconds)
CONNECT_TIMEOUT   = 10.0
TRANSFER_TIMEOUT  = 60.0
ACCEPT_TIMEOUT    = 1.0            # accept() poll interval (lets stop() work)

# Storage
import os
SAVE_DIR          = os.path.abspath("received_files")
DEVICE_FILE       = os.path.expanduser("~/.lan_share_device.json")

# Protocol
PROTOCOL_VERSION  = 2
