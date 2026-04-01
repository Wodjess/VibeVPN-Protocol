#!/usr/bin/env python3
"""
HTTPS VPN Server — runs inside Docker container.
Supports multiple simultaneous clients with multi-queue TUN.
"""

import asyncio
import logging
import os
import signal
import ssl
import subprocess
import sys

import websockets

from common import (
    SERVER_TUN_IP,
    TUN_MTU,
    CLIENT_IP_START,
    CLIENT_IP_END,
    get_dst_ip,
    get_src_ip,
)
from users import authenticate
from tun_linux import MultiQueueTun

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SERVER] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Number of TUN queues (parallel readers)
NUM_TUN_QUEUES = int(os.environ.get("VPN_TUN_QUEUES", "4"))


def configure_tun(tun_name: str, local_ip: str, mtu: int):
    subprocess.run(
        ["ip", "addr", "add", f"{local_ip}/24", "dev", tun_name], check=True
    )
    subprocess.run(
        ["ip", "link", "set", "dev", tun_name, "mtu", str(mtu), "up"], check=True
    )
    log.info("TUN %s configured: %s/24", tun_name, local_ip)


def enable_ip_forwarding():
    try:
        subprocess.run(
            ["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
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
        ["iptables", "-A", "FORWARD", "-s", tun_subnet, "-o", iface, "-j", "ACCEPT"],
        check=True,
    )
    subprocess.run(
        ["iptables", "-A", "FORWARD", "-d", tun_subnet, "-i", iface,
         "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
        check=True,
    )
    subprocess.run(
        [
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-s", tun_subnet, "-o", iface, "-j", "MASQUERADE",
        ],
        check=True,
    )
    log.info("NAT configured: %s via %s (FORWARD + MASQUERADE)", tun_subnet, iface)


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
    def __init__(self, cert: str, key: str, host: str, port: int):
        self.cert = cert
        self.key = key
        self.host = host
        self.port = port
        self.tun = MultiQueueTun("vpn0", NUM_TUN_QUEUES)
        self.ip_pool = IPPool()
        self.clients: dict[str, websockets.WebSocketServerProtocol] = {}
        self._write_queue = 0  # round-robin write queue

    async def start(self):
        self.tun.open()
        configure_tun("vpn0", SERVER_TUN_IP, TUN_MTU)
        enable_ip_forwarding()
        setup_nat("10.8.0.0/24")

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(self.cert, self.key)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_3

        log.info(
            "WSS server listening on %s:%d (%d TUN queues)",
            self.host, self.port, NUM_TUN_QUEUES,
        )
        async with websockets.serve(
            self.handle_client,
            self.host,
            self.port,
            ssl=ssl_ctx,
            max_size=2 ** 16,
            ping_interval=20,
            ping_timeout=60,
        ):
            # Launch one reader per TUN queue
            readers = [
                asyncio.create_task(self.tun_to_ws(q))
                for q in range(NUM_TUN_QUEUES)
            ]
            await asyncio.gather(*readers)

    def _write_tun(self, data: bytes):
        """Write packet to TUN, round-robin across queues."""
        q = self._write_queue
        self._write_queue = (q + 1) % NUM_TUN_QUEUES
        self.tun.write(data, queue=q)

    async def handle_client(self, ws):
        # Auth: client sends "username:password"
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            credentials = raw.decode() if isinstance(raw, bytes) else raw
            if ":" not in credentials:
                log.warning("Malformed auth from %s", ws.remote_address)
                await ws.close(4001, "Unauthorized")
                return
            username, password = credentials.split(":", 1)
            if not authenticate(username, password):
                log.warning("Auth failed for user '%s' from %s", username, ws.remote_address)
                await ws.close(4001, "Unauthorized")
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
        self.clients[client_ip] = ws

        log.info(
            "Client connected: %s [%s] -> %s (%d active)",
            username, ws.remote_address, client_ip, self.ip_pool.active_count,
        )

        try:
            async for message in ws:
                if not isinstance(message, bytes) or len(message) == 0:
                    continue
                first_byte = message[0]
                if (first_byte >> 4) in (4, 6):
                    if len(message) >= 20:
                        src_ip = get_src_ip(message)
                        if src_ip == client_ip:
                            self._write_tun(message)
                else:
                    offset = 0
                    while offset + 2 <= len(message):
                        pkt_len = int.from_bytes(message[offset:offset+2], "big")
                        offset += 2
                        if offset + pkt_len > len(message):
                            break
                        pkt = message[offset:offset+pkt_len]
                        if len(pkt) >= 20:
                            src_ip = get_src_ip(pkt)
                            if src_ip == client_ip:
                                self._write_tun(pkt)
                        offset += pkt_len
        except websockets.ConnectionClosed:
            pass
        finally:
            if self.clients.get(client_ip) is ws:
                del self.clients[client_ip]
            self.ip_pool.release(client_ip)
            log.info(
                "Client disconnected: %s [%s] (%s, %d active)",
                username, ws.remote_address, client_ip, self.ip_pool.active_count,
            )

    async def tun_to_ws(self, queue_id: int):
        """Read packets from one TUN queue and dispatch to clients."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.run_in_executor(
                    None, self.tun.read, queue_id, TUN_MTU + 100
                )
                if not data or len(data) < 20:
                    continue
                dst_ip = get_dst_ip(data)
                src_ip = get_src_ip(data)
                if dst_ip is None:
                    continue
                # Block inter-client traffic
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
                        asyncio.create_task(ws.send(data))
                    except websockets.ConnectionClosed:
                        pass
            except OSError:
                await asyncio.sleep(0.05)


def main():
    cert = os.environ.get("VPN_CERT", "/etc/vpn/cert.pem")
    key = os.environ.get("VPN_KEY", "/etc/vpn/key.pem")
    host = os.environ.get("VPN_HOST", "0.0.0.0")
    port = int(os.environ.get("VPN_PORT", "443"))

    server = VPNServer(cert, key, host, port)

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
