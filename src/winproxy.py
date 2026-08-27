"""Windows-side TCP relay for WSL OpenSSH ProxyCommand.

Run with Windows python.exe so the TCP path uses the Windows network
stack (same as Tabby). Speaks raw bytes on stdin/stdout.
"""

import os
import socket
import sys
import threading


def _pump_in(sock):
    try:
        while True:
            data = os.read(sys.stdin.buffer.fileno(), 8192)
            if not data:
                break
            sock.sendall(data)
    except Exception:
        pass
    try:
        sock.shutdown(socket.SHUT_WR)
    except Exception:
        pass


def _pump_out(sock):
    try:
        while True:
            data = sock.recv(8192)
            if not data:
                break
            os.write(sys.stdout.buffer.fileno(), data)
    except Exception:
        pass


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: winproxy.py HOST PORT\n")
        return 2
    sock = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=15)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    thread = threading.Thread(target=_pump_out, args=(sock,), daemon=True)
    thread.start()
    _pump_in(sock)
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
