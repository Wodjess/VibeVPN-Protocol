"""TUN interface for Linux (server side) with multi-queue support."""

import os
import struct
import fcntl

# Linux ioctl constants
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
IFF_MULTI_QUEUE = 0x0100


class TunDevice:
    """Single-queue Linux TUN device."""

    def __init__(self, name: str = "tun0"):
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


class MultiQueueTun:
    """Multi-queue Linux TUN — opens N file descriptors to the same interface.

    The kernel distributes incoming packets across queues (readers),
    allowing parallel reads without contention.
    """

    def __init__(self, name: str = "vpn0", num_queues: int = 4):
        self.name = name
        self.num_queues = num_queues
        self.fds: list[int] = []

    def open(self):
        for _ in range(self.num_queues):
            fd = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK)
            ifr = struct.pack(
                "16sH",
                self.name.encode(),
                IFF_TUN | IFF_NO_PI | IFF_MULTI_QUEUE,
            )
            fcntl.ioctl(fd, TUNSETIFF, ifr)
            self.fds.append(fd)

    def read(self, queue: int, bufsize: int = 65535) -> bytes:
        """Read from a specific queue. Blocks until a packet arrives."""
        fd = self.fds[queue]
        # fd is O_NONBLOCK, use select to wait
        import select
        select.select([fd], [], [])
        return os.read(fd, bufsize)

    def write(self, data: bytes, queue: int = 0) -> int:
        """Write to a specific queue."""
        return os.write(self.fds[queue], data)

    def close(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()
