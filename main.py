"""
Entry point for LAN Share.

Usage:
    python main.py
    python main.py --name "Ahmad-Laptop"
    python main.py --tcp-port 5002
    python main.py --save-dir D:\\downloads
"""

import argparse
import os
import sys

from config import SAVE_DIR, TCP_PORT
from ui import LanShareApp
from utils import Device


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN Share — TCP file transfer")
    parser.add_argument("--name",     help="Friendly device name shown to peers")
    parser.add_argument("--save-dir", default=SAVE_DIR,
                        help="Directory where received files are saved")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT,
                        help="TCP port the receive server listens on")
    args = parser.parse_args()

    os.makedirs(args.save_dir, exist_ok=True)
    device = Device.load_or_create(override_name=args.name)

    app = LanShareApp(
        device,
        save_dir=os.path.abspath(args.save_dir),
        tcp_port=args.tcp_port,
    )
    app.mainloop()


if __name__ == "__main__":
    sys.exit(main())
