"""
Pluggable CSI input source: serial port, TCP socket (simulator), or file.

Selected by the `SERIAL_PORT` env var:
  /dev/cu.usbmodem14101    → real serial
  tcp://127.0.0.1:3333     → simulator over TCP
  file:///path/to/cap.csv  → replay a captured CSV file

All three return an iterator of newline-terminated str lines.
"""

from __future__ import annotations

import socket
import time
from typing import Iterator, Optional


def open_source(spec: str, baud: int = 921600) -> Iterator[str]:
    """Yield CSI lines from the source described by `spec`."""
    if spec.startswith("tcp://"):
        return _tcp_source(spec)
    if spec.startswith("file://"):
        return _file_source(spec[len("file://") :])
    return _serial_source(spec, baud)


def _serial_source(port: str, baud: int) -> Iterator[str]:
    import serial  # imported lazily so tests don't need pyserial

    while True:
        try:
            with serial.Serial(port, baud, timeout=1) as ser:
                buf = b""
                while True:
                    chunk = ser.read(1024)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            yield line.decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[source] serial error: {e}; reconnecting in 2s")
            time.sleep(2)


def _tcp_source(spec: str) -> Iterator[str]:
    rest = spec[len("tcp://") :]
    if ":" not in rest:
        raise ValueError(f"tcp:// spec must include port: {spec}")
    host, port_str = rest.rsplit(":", 1)
    port = int(port_str)

    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            s.settimeout(2.0)
            buf = b""
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    yield line.decode("utf-8", errors="ignore")
            s.close()
        except Exception as e:
            print(f"[source] tcp error: {e}; reconnecting in 2s")
            time.sleep(2)


def _file_source(path: str) -> Iterator[str]:
    """Replay a captured CSV file at simulated 20 Hz."""
    while True:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                yield line.rstrip("\n")
                time.sleep(1.0 / 20.0)
        time.sleep(0.5)
