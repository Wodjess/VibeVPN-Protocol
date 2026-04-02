"""Shared constants and utilities for HTTPS VPN tunnel."""

# Network config
TUN_MTU = 1400
SERVER_TUN_IP = "10.8.0.1"
TUN_SUBNET = "10.8.0.0/24"
CLIENT_IP_START = 2
CLIENT_IP_END = 254
MAX_CLIENTS = CLIENT_IP_END - CLIENT_IP_START + 1


def get_dst_ip(packet: bytes) -> str | None:
    """Extract destination IP from an IPv4 packet."""
    if len(packet) < 20:
        return None
    version = (packet[0] >> 4) & 0xF
    if version != 4:
        return None
    return f"{packet[16]}.{packet[17]}.{packet[18]}.{packet[19]}"


def get_src_ip(packet: bytes) -> str | None:
    """Extract source IP from an IPv4 packet."""
    if len(packet) < 20:
        return None
    version = (packet[0] >> 4) & 0xF
    if version != 4:
        return None
    return f"{packet[12]}.{packet[13]}.{packet[14]}.{packet[15]}"
