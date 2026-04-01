#!/usr/bin/env python3
"""
HTTPS VPN Server — runs inside Docker container.
Supports multiple simultaneous clients.
"""

import asyncio
import hashlib
import logging
import os
import signal
import ssl
import subprocess
import sys
import threading
import time

import websockets

from common import (
    SERVER_TUN_IP,
    TUN_MTU,
    CLIENT_IP_START,
    CLIENT_IP_END,
    get_dst_ip,
    get_src_ip,
    verify_auth_token,
)
from tun_linux import TunDevice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SERVER] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def configure_tun(tun_name: str, local_ip: str, mtu: int):
    subprocess.run(
        ["ip", "addr", "add", f"{local_ip}/24", "dev", tun_name], check=True
    )
    subprocess.run(
        ["ip", "link", "set", "dev", tun_name, "mtu", str(mtu), "up"], check=True
    )
    log.info("TUN %s configured: %s/24", tun_name, local_ip)


def enable_ip_forwarding():
    """Enable IP forwarding. Falls back gracefully inside Docker
    where --sysctl net.ipv4.ip_forward=1 is set at container start."""
    try:
        subprocess.run(
            ["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        # Already enabled via Docker --sysctl flag
        pass
    log.info("IP forwarding enabled")


def setup_nat(tun_subnet: str):
    result = subprocess.run(
        ["ip", "route", "show", "default"],
        capture_output=True, text=True, check=True,
    )
    parts = result.stdout.split()
    iface = parts[parts.index("dev") + 1] if "dev" in parts else "eth0"

    subprocess.run(
        [
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-s", tun_subnet, "-o", iface, "-j", "MASQUERADE",
        ],
        check=True,
    )
    log.info("NAT configured: %s via %s", tun_subnet, iface)


class NonceTracker:
    NONCE_EXPIRY = 300

    def __init__(self):
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_record(self, token: str) -> bool:
        try:
            nonce = token.split(":")[0]
        except (ValueError, AttributeError):
            return False
        now = time.time()
        with self._lock:
            expired = [n for n, t in self._used.items() if now - t > self.NONCE_EXPIRY]
            for n in expired:
                del self._used[n]
            if nonce in self._used:
                return False
            self._used[nonce] = now
            return True


class IPPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._available: set[int] = set(range(CLIENT_IP_START, CLIENT_IP_END + 1))
        self._allocated: dict[int, str] = {}

    def allocate(self, ws_id: str) -> str | None:
        with self._lock:
            if not self._available:
                return None
            octet = min(self._available)
            self._available.discard(octet)
            self._allocated[octet] = ws_id
            return f"10.8.0.{octet}"

    def release(self, ip: str):
        parts = ip.split(".")
        if len(parts) != 4:
            return
        octet = int(parts[3])
        with self._lock:
            self._allocated.pop(octet, None)
            if CLIENT_IP_START <= octet <= CLIENT_IP_END:
                self._available.add(octet)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._allocated)


class VPNServer:
    def __init__(self, secret: str, cert: str, key: str, host: str, port: int):
        self.secret = secret
        self.cert = cert
        self.key = key
        self.host = host
        self.port = port
        self.tun = TunDevice("vpn0")
        self.nonce_tracker = NonceTracker()
        self.ip_pool = IPPool()
        self.clients: dict[str, websockets.WebSocketServerProtocol] = {}
        self._clients_lock = asyncio.Lock()

    async def start(self):
        self.tun.open()
        configure_tun("vpn0", SERVER_TUN_IP, TUN_MTU)
        enable_ip_forwarding()
        setup_nat("10.8.0.0/24")

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(self.cert, self.key)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_3

        log.info("WSS server listening on %s:%d", self.host, self.port)
        async with websockets.serve(
            self.handle_client,
            self.host,
            self.port,
            ssl=ssl_ctx,
            max_size=2 ** 16,
            ping_interval=20,
            ping_timeout=60,
        ):
            await self.tun_to_ws()

    async def handle_client(self, ws):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            token = raw.decode() if isinstance(raw, bytes) else raw
            if not verify_auth_token(token, self.secret, debug=True):
                log.warning("Auth failed from %s (token=%s, secret_hash=%s)",
                            ws.remote_address, token[:16] + "...",
                            hashlib.sha256(self.secret.encode()).hexdigest()[:16])
                await ws.close(4001, "Unauthorized")
                return
            if not self.nonce_tracker.check_and_record(token):
                log.warning("Replay attack detected from %s", ws.remote_address)
                await ws.close(4003, "Replay detected")
                return
        except (asyncio.TimeoutError, websockets.ConnectionClosed, ValueError) as e:
            log.warning("Auth error: %s", e)
            return

        ws_id = f"{ws.remote_address}"
        client_ip = self.ip_pool.allocate(ws_id)
        if client_ip is None:
            log.warning("IP pool exhausted, rejecting %s", ws.remote_address)
            await ws.close(4002, "No IPs available")
            return

        await ws.send(client_ip.encode())

        async with self._clients_lock:
            self.clients[client_ip] = ws

        log.info(
            "Client connected: %s -> %s (%d active)",
            ws.remote_address, client_ip, self.ip_pool.active_count,
        )

        try:
            async for message in ws:
                if isinstance(message, bytes) and len(message) >= 20:
                    src_ip = get_src_ip(message)
                    if src_ip == client_ip:
                        self.tun.write(message)
        except websockets.ConnectionClosed:
            pass
        finally:
            async with self._clients_lock:
                if self.clients.get(client_ip) is ws:
                    del self.clients[client_ip]
            self.ip_pool.release(client_ip)
            log.info(
                "Client disconnected: %s (%s, %d active)",
                ws.remote_address, client_ip, self.ip_pool.active_count,
            )

    async def tun_to_ws(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, self.tun.read, TUN_MTU + 100)
                if not data or len(data) < 20:
                    continue
                dst_ip = get_dst_ip(data)
                src_ip = get_src_ip(data)
                if dst_ip is None:
                    continue
                # Block inter-client traffic (both IPs in tunnel subnet)
                if (
                    src_ip and src_ip.startswith("10.8.0.")
                    and dst_ip.startswith("10.8.0.")
                    and dst_ip != SERVER_TUN_IP
                    and src_ip != SERVER_TUN_IP
                ):
                    continue
                ws = self.clients.get(dst_ip)
                if ws is not None:
                    try:
                        await ws.send(data)
                    except websockets.ConnectionClosed:
                        pass
            except OSError:
                await asyncio.sleep(0.1)


def main():
    secret = os.environ.get("VPN_SECRET", "").strip()
    cert = os.environ.get("VPN_CERT", "/etc/vpn/cert.pem")
    key = os.environ.get("VPN_KEY", "/etc/vpn/key.pem")
    host = os.environ.get("VPN_HOST", "0.0.0.0")
    port = int(os.environ.get("VPN_PORT", "443"))

    if not secret:
        print("Error: VPN_SECRET environment variable is required", file=sys.stderr)
        sys.exit(1)

    server = VPNServer(secret, cert, key, host, port)

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: loop.stop())

    try:
        loop.run_until_complete(server.start())
    except KeyboardInterrupt:
        pass
    finally:
        server.tun.close()
        log.info("Server stopped")


if __name__ == "__main__":
    main()
