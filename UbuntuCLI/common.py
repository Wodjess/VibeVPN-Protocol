"""Shared constants and utilities for HTTPS VPN tunnel."""

# Network config
TUN_MTU = 1400
SERVER_TUN_IP = "10.8.0.1"
TUN_SUBNET = "10.8.0.0/24"
CLIENT_IP_START = 2
CLIENT_IP_END = 254
MAX_CLIENTS = CLIENT_IP_END - CLIENT_IP_START + 1

# Pre-computed bytes for fast subnet check in hot path
_SUBNET_PREFIX = bytes([10, 8, 0])
_SERVER_IP_BYTES = bytes([10, 8, 0, 1])


def get_dst_ip(packet: bytes) -> str | None:
    """Extract destination IP from an IPv4 packet."""
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None
    return f"{packet[16]}.{packet[17]}.{packet[18]}.{packet[19]}"


def get_src_ip(packet: bytes) -> str | None:
    """Extract source IP from an IPv4 packet."""
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None
    return f"{packet[12]}.{packet[13]}.{packet[14]}.{packet[15]}"


def is_tunnel_ip(packet: bytes, offset: int) -> bool:
    """Check if IP at offset is in 10.8.0.0/24 (fast bytes check)."""
    return packet[offset:offset+3] == _SUBNET_PREFIX


def is_server_ip(packet: bytes, offset: int) -> bool:
    """Check if IP at offset is the server TUN IP 10.8.0.1."""
    return packet[offset:offset+4] == _SERVER_IP_BYTES
