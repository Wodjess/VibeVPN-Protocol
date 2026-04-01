#!/usr/bin/env python3
"""
HTTPS VPN Client — runs on your macOS machine.

Usage:
    sudo python3 client.py --server your-server.com --secret YOUR_SECRET
"""

import argparse
import asyncio
import logging
import os
import platform
import re
import signal
import ssl
import subprocess
import sys

import websockets

from common import (
    SERVER_TUN_IP,
    TUN_MTU,
    make_auth_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLIENT] %(message)s")
log = logging.getLogger(__name__)


def get_default_gateway() -> tuple[str, str]:
    """Get the current default gateway IP and interface."""
    result = subprocess.run(
        ["route", "-n", "get", "default"], capture_output=True, text=True
    )
    gateway = None
    iface = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("gateway:"):
            gateway = line.split(":")[1].strip()
        if line.startswith("interface:"):
            iface = line.split(":")[1].strip()
    return gateway or "192.168.1.1", iface or "en0"


def resolve_server(hostname: str) -> str:
    import socket
    return socket.gethostbyname(hostname)


def configure_tun_darwin(tun_name: str, local_ip: str, peer_ip: str, mtu: int):
    subprocess.run(
        ["ifconfig", tun_name, local_ip, peer_ip, "mtu", str(mtu), "up"],
        check=True,
    )
    log.info("TUN %s configured: %s -> %s", tun_name, local_ip, peer_ip)


def setup_routes(server_ip: str, gateway: str, gateway_iface: str, tun_name: str):
    subprocess.run(["route", "add", "-host", server_ip, gateway], check=True)
    # IPv4: 0/1 + 128/1 covers all addresses, more specific than default 0/0
    subprocess.run(
        ["route", "add", "-net", "0.0.0.0/1", "-interface", tun_name], check=True
    )
    subprocess.run(
        ["route", "add", "-net", "128.0.0.0/1", "-interface", tun_name], check=True
    )
    # IPv6: block to prevent leaks (blackhole routes)
    subprocess.run(
        ["route", "add", "-inet6", "-net", "::/1", "-blackhole"], capture_output=True
    )
    subprocess.run(
        ["route", "add", "-inet6", "-net", "8000::/1", "-blackhole"], capture_output=True
    )
    log.info(
        "Routes configured: all traffic -> %s, server %s -> %s, IPv6 blocked",
        tun_name, server_ip, gateway,
    )


def teardown_routes(server_ip: str):
    for cmd in [
        ["route", "delete", "-net", "0.0.0.0/1"],
        ["route", "delete", "-net", "128.0.0.0/1"],
        ["route", "delete", "-host", server_ip],
        ["route", "delete", "-inet6", "-net", "::/1"],
        ["route", "delete", "-inet6", "-net", "8000::/1"],
    ]:
        subprocess.run(cmd, capture_output=True)
    log.info("Routes cleaned up")


def setup_dns(dns_server: str = "1.1.1.1"):
    result = subprocess.run(
        ["networksetup", "-listallnetworkservices"],
        capture_output=True, text=True,
    )
    services = [
        line.strip() for line in result.stdout.splitlines()[1:]
        if not line.startswith("*")
    ]
    for svc in services:
        subprocess.run(
            ["networksetup", "-setdnsservers", svc, dns_server],
            capture_output=True,
        )
    log.info("DNS set to %s", dns_server)


def restore_dns():
    result = subprocess.run(
        ["networksetup", "-listallnetworkservices"],
        capture_output=True, text=True,
    )
    for svc in result.stdout.splitlines()[1:]:
        svc = svc.strip()
        if svc and not svc.startswith("*"):
            subprocess.run(
                ["networksetup", "-setdnsservers", svc, "empty"],
                capture_output=True,
            )


def make_ssl_context(insecure: bool = False) -> ssl.SSLContext:
    """Create SSL context for WebSocket connection."""
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class VPNClient:
    def __init__(self, server: str, port: int, secret: str, insecure: bool = False):
        self.server = server
        self.port = port
        self.secret = secret
        self.insecure = insecure
        self.server_ip = resolve_server(server)
        self.gateway, self.gateway_iface = get_default_gateway()
        self.tun = None
        self.tun_name = None
        self.ws = None
        self.running = False
        self._cleaned_up = False

    def create_tun(self, assigned_ip: str):
        from tun_darwin import TunDevice
        for unit in range(5, 20):
            try:
                self.tun = TunDevice(unit=unit)
                self.tun.open()
                self.tun_name = f"utun{unit}"
                log.info("Opened %s", self.tun_name)
                break
            except OSError:
                continue
        else:
            raise RuntimeError("Could not open any utun device")

        configure_tun_darwin(self.tun_name, assigned_ip, SERVER_TUN_IP, TUN_MTU)

    async def connect(self) -> str:
        uri = f"wss://{self.server}:{self.port}"
        log.info("Connecting to %s (resolved: %s)", uri, self.server_ip)

        ssl_ctx = make_ssl_context(self.insecure)
        self.ws = await websockets.connect(
            uri,
            ssl=ssl_ctx,
            max_size=2 ** 16,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=5,
        )

        token = make_auth_token(self.secret)
        await self.ws.send(token)

        response = await asyncio.wait_for(self.ws.recv(), timeout=10)
        if isinstance(response, bytes):
            response = response.decode()
        assigned_ip = response.strip()

        if not re.match(r"^10\.8\.0\.(\d{1,3})$", assigned_ip):
            raise ValueError(f"Server sent invalid IP: {assigned_ip}")
        last_octet = int(assigned_ip.rsplit(".", 1)[1])
        if not (2 <= last_octet <= 254):
            raise ValueError(f"Server sent out-of-range IP: {assigned_ip}")

        log.info("Connected, assigned IP: %s", assigned_ip)
        return assigned_ip

    async def tun_to_ws(self):
        loop = asyncio.get_running_loop()
        while self.running:
            try:
                data = await loop.run_in_executor(None, self.tun.read, TUN_MTU + 100)
                if data and self.ws:
                    await self.ws.send(data)
            except OSError:
                if self.running:
                    await asyncio.sleep(0.1)
            except websockets.ConnectionClosed:
                log.warning("WebSocket closed during send")
                break

    async def ws_to_tun(self):
        try:
            async for message in self.ws:
                if isinstance(message, bytes) and len(message) > 0:
                    self.tun.write(message)
        except websockets.ConnectionClosed:
            log.warning("WebSocket closed during receive")

    def reconfigure_tun_ip(self, new_ip: str):
        subprocess.run(
            ["ifconfig", self.tun_name, new_ip, SERVER_TUN_IP],
            check=True,
        )
        log.info("TUN %s reconfigured to %s", self.tun_name, new_ip)

    async def run(self):
        self.running = True
        tun_configured = False
        current_ip = None

        try:
            while self.running:
                try:
                    assigned_ip = await self.connect()

                    if not tun_configured:
                        self.create_tun(assigned_ip)
                        setup_routes(
                            self.server_ip, self.gateway,
                            self.gateway_iface, self.tun_name,
                        )
                        setup_dns()
                        tun_configured = True
                        current_ip = assigned_ip
                    elif assigned_ip != current_ip:
                        self.reconfigure_tun_ip(assigned_ip)
                        current_ip = assigned_ip

                    await asyncio.gather(
                        self.tun_to_ws(),
                        self.ws_to_tun(),
                    )
                except (
                    ConnectionError,
                    websockets.ConnectionClosed,
                    websockets.WebSocketException,
                    OSError,
                    ValueError,
                ) as e:
                    if not self.running:
                        break
                    log.warning("Connection lost: %s. Reconnecting in 3s...", e)
                    await asyncio.sleep(3)
        finally:
            self.cleanup()

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self.running = False
        teardown_routes(self.server_ip)
        if self.tun:
            self.tun.close()
        restore_dns()
        log.info("Cleanup complete")


def main():
    parser = argparse.ArgumentParser(description="HTTPS VPN Client")
    parser.add_argument("--server", required=True, help="VPN server hostname")
    parser.add_argument("--port", type=int, default=443, help="Server port")
    parser.add_argument("--secret", help="Shared secret (or use --secret-file)")
    parser.add_argument("--secret-file", help="Read secret from file")
    parser.add_argument(
        "--insecure", action="store_true",
        help="Skip TLS certificate verification (for self-signed certs)",
    )
    args = parser.parse_args()

    # Resolve secret
    secret = args.secret
    if args.secret_file:
        with open(args.secret_file) as f:
            secret = f.read().strip()
    if not secret:
        print("Error: --secret or --secret-file is required", file=sys.stderr)
        sys.exit(1)

    if os.geteuid() != 0:
        print("Error: client must run as root (sudo)", file=sys.stderr)
        sys.exit(1)

    client = VPNClient(args.server, args.port, secret, insecure=args.insecure)

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(client, "running", False))

    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        pass
    finally:
        client.cleanup()


if __name__ == "__main__":
    main()
