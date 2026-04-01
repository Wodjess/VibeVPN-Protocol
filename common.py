"""Shared constants and utilities for HTTPS VPN tunnel."""

import hashlib
import hmac
import os

# Network config
TUN_MTU = 1400
SERVER_TUN_IP = "10.8.0.1"
TUN_SUBNET = "10.8.0.0/24"
# Client IP pool: 10.8.0.2 — 10.8.0.254
CLIENT_IP_START = 2
CLIENT_IP_END = 254
MAX_CLIENTS = CLIENT_IP_END - CLIENT_IP_START + 1


def derive_key(secret: str) -> bytes:
    """Derive an auth key from shared secret."""
    return hashlib.sha256(secret.encode()).digest()


def verify_auth_token(token: str, secret: str, debug: bool = False) -> bool:
    """Verify an auth token."""
    try:
        nonce, sig = token.split(":")
        key = derive_key(secret)
        expected = hmac.new(key, nonce.encode(), hashlib.sha256).hexdigest()
        match = hmac.compare_digest(sig, expected)
        if debug and not match:
            import sys
            print(f"[AUTH DEBUG] token type={type(token).__name__}, len={len(token)}", file=sys.stderr)
            print(f"[AUTH DEBUG] nonce={nonce!r} ({len(nonce)} chars)", file=sys.stderr)
            print(f"[AUTH DEBUG] sig={sig[:16]}... ({len(sig)} chars)", file=sys.stderr)
            print(f"[AUTH DEBUG] expected={expected[:16]}... ({len(expected)} chars)", file=sys.stderr)
            print(f"[AUTH DEBUG] secret={secret[:8]}...{secret[-4:]} ({len(secret)} chars)", file=sys.stderr)
        return match
    except (ValueError, AttributeError) as e:
        if debug:
            import sys
            print(f"[AUTH DEBUG] parse error: {e}, token={token!r}", file=sys.stderr)
        return False


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
