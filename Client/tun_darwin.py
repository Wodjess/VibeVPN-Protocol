"""TUN interface for macOS (client side) using utun."""

import os
import socket
import struct
import ctypes
import ctypes.util


# macOS utun constants
UTUN_CONTROL_NAME = b"com.apple.net.utun_control"
CTLIOCGINFO = 0xC0644E03  # _IOWR('N', 3, struct ctl_info)
AF_SYS_CONTROL = 2
SYSPROTO_CONTROL = 2
PF_SYSTEM = socket.AF_SYSTEM if hasattr(socket, "AF_SYSTEM") else 32


class CtlInfo(ctypes.Structure):
    _fields_ = [
        ("ctl_id", ctypes.c_uint32),
        ("ctl_name", ctypes.c_char * 96),
    ]


class SockaddrCtl(ctypes.Structure):
    _fields_ = [
        ("sc_len", ctypes.c_uint8),
        ("sc_family", ctypes.c_uint8),
        ("ss_sysaddr", ctypes.c_uint16),
        ("sc_id", ctypes.c_uint32),
        ("sc_unit", ctypes.c_uint32),
        ("sc_reserved", ctypes.c_uint32 * 5),
    ]


class TunDevice:
    """macOS utun device wrapper."""

    def __init__(self, unit: int = 0):
        """unit=0 creates utun0, unit=1 creates utun1, etc."""
        self.unit = unit
        self.name = f"utun{unit}"
        self.sock = None

    def open(self) -> int:
        self.sock = socket.socket(PF_SYSTEM, socket.SOCK_DGRAM, SYSPROTO_CONTROL)

        # Get the control ID for utun
        info = CtlInfo()
        info.ctl_name = UTUN_CONTROL_NAME
        import fcntl
        fcntl.ioctl(self.sock.fileno(), CTLIOCGINFO, info)

        # Connect to the utun kernel control
        addr = SockaddrCtl()
        addr.sc_len = ctypes.sizeof(SockaddrCtl)
        addr.sc_family = PF_SYSTEM
        addr.ss_sysaddr = AF_SYS_CONTROL
        addr.sc_id = info.ctl_id
        addr.sc_unit = self.unit + 1  # 1-based in kernel

        self.sock.connect(
            bytes(addr)[: ctypes.sizeof(SockaddrCtl)]
        )
        return self.sock.fileno()

    def read(self, bufsize: int = 65535) -> bytes:
        """Read a packet. macOS utun prepends a 4-byte protocol header."""
        data = self.sock.recv(bufsize)
        if len(data) <= 4:
            return b""
        # Skip the 4-byte AF header
        return data[4:]

    def write(self, data: bytes) -> int:
        """Write a packet. Must prepend 4-byte AF header."""
        if not data:
            return 0
        # Determine AF type from IP version
        version = (data[0] >> 4) & 0xF
        if version == 4:
            af = socket.AF_INET
        elif version == 6:
            af = socket.AF_INET6
        else:
            return 0
        header = struct.pack("I", af)  # host byte order for macOS utun
        return self.sock.send(header + data)

    def fileno(self) -> int:
        return self.sock.fileno()

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None
