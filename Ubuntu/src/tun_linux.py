"""TUN interface for Linux (client side)."""

import os
import struct
import fcntl

# Linux ioctl constants
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000


class TunDevice:
    """Single-queue Linux TUN device for client use."""

    def __init__(self, name: str = "vibevpn0"):
        self.name = name
        self.fd = None

    def open(self) -> int:
        self.fd = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack("16sH", self.name.encode(), IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(self.fd, TUNSETIFF, ifr)
        return self.fd

    def read(self, bufsize: int = 65535) -> bytes:
        return os.read(self.fd, bufsize)

    def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    def fileno(self) -> int:
        return self.fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
