#!/usr/bin/env python3
"""Test an SSH connection to Unraid from the command line.

Separates "can I reach the box", "can I authenticate", "is GNU find there" and
"can I see the disks", so a failure in the UI can be pinned to one of them.

    ./scripts/check-ssh.py 192.168.1.10 --user root --ask-password
"""

from __future__ import annotations

import argparse
import getpass
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.backends.base import StorageError  # noqa: E402
from app.backends.ssh import SSHBackend  # noqa: E402


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--key")
    parser.add_argument("--mount-root", default="/mnt")
    parser.add_argument("--verbose", action="store_true", help="show the paramiko log")
    args = parser.parse_args()

    if args.verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    password = args.password
    if args.ask_password and not password:
        password = getpass.getpass(f"Password for {args.user}@{args.host}: ")

    print(f"1. TCP {args.host}:{args.port} ... ", end="", flush=True)
    try:
        with socket.create_connection((args.host, args.port), timeout=10) as sock:
            banner = sock.recv(256).decode("utf-8", "replace").strip()
        print(f"open   ({banner or 'no banner'})")
    except OSError as exc:
        print(f"FAILED: {exc}")
        print("\n   The box is not reachable on that port. Check the address, that")
        print("   it is powered on, and that SSH is enabled in Unraid under")
        print("   Settings -> Management Access.")
        return 1

    backend = SSHBackend(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        key_path=args.key or "",
        mount_root=args.mount_root,
    )

    auth = "password" if password else f"key {args.key}" if args.key else "agent/default keys"
    print(f"2. auth as {args.user} using {auth} ... ", end="", flush=True)
    try:
        rc, out, _ = backend._run("id -un")  # noqa: SLF001 - a diagnostic, by design
        print(f"ok     (remote user: {out.strip() or rc})")
    except StorageError as exc:
        print("FAILED")
        print(f"\n   {exc}")
        return 1

    print("3. GNU find ... ", end="", flush=True)
    rc, out, _ = backend._run("find --version 2>/dev/null | head -1")  # noqa: SLF001
    print(out.strip() if "GNU" in out else "NOT FOUND - fast scanning needs `find -printf`")

    print(f"4. disks under {args.mount_root} ... ", end="", flush=True)
    try:
        disks = backend.list_disks()
    except StorageError as exc:
        print(f"FAILED: {exc}")
        return 1
    if not disks:
        print("none found")
        rc, out, _ = backend._run(f"ls -1 {args.mount_root}")  # noqa: SLF001
        print(f"\n   {args.mount_root} contains: {' '.join(out.split()) or '(nothing)'}")
        print("   Shuffler looks for directories named disk1, disk2, ...")
        return 1
    print(f"{len(disks)} found")
    for disk in disks:
        pct = (disk.used / disk.total * 100) if disk.total else 0
        print(f"     {disk.name:8} {human(disk.used):>10} used of {human(disk.total):>10} ({pct:.0f}%)")

    print("\nAll checks passed. These settings will work in the UI.")
    backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
