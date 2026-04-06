#!/usr/bin/env python3
"""
VibeVPN CLI Client for Ubuntu.

Creates a local TUN interface to access the VPN network (10.8.0.0/24)
without proxying all system traffic. Useful for accessing services
and ports that are only available within the VPN.

Usage:
  1. Edit config.json with your server details
  2. sudo python3 vibevpn.py

Requires: python3, websockets (pip install websockets)
Must run as root (TUN device requires privileges).
"""

import asyncio
import json
import logging
import os
import signal
import socket
import ssl
import subprocess
import sys

from common import TUN_MTU, TUN_SUBNET
from tun_linux import TunDevice

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [VibeVPN] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

TUN_NAME = "vibevpn0"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# ── Config ────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        log.error("config.json not found. Create it next to vibevpn.py")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("Invalid config.json: %s", e)
        sys.exit(1)

    for key in ("server", "username", "password"):
        if not cfg.get(key):
            log.error("Missing required field '%s' in config.json", key)
            sys.exit(1)

    return cfg

# ── Network helpers ───────────────────────────────────────────────────

def resolve_server(hostname: str) -> str:
    return socket.gethostbyname(hostname)


def setup_tun(tun_name: str, assigned_ip: str):
    """Configure TUN interface with assigned IP, route only VPN subnet."""
    subprocess.run(
        ["ip", "addr", "add", f"{assigned_ip}/24", "dev", tun_name],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ip", "link", "set", tun_name, "mtu", str(TUN_MTU), "up"],
        check=True, capture_output=True,
    )
    # Only route VPN subnet through TUN — no default route override
    subprocess.run(
        ["ip", "route", "add", TUN_SUBNET, "dev", tun_name],
        capture_output=True,
    )
    log.info("TUN %s configured: %s, route %s", tun_name, assigned_ip, TUN_SUBNET)


def update_tun_ip(tun_name: str, new_ip: str):
    """Update TUN IP if it changed on reconnect."""
    subprocess.run(["ip", "addr", "flush", "dev", tun_name], capture_output=True)
    subprocess.run(
        ["ip", "addr", "add", f"{new_ip}/24", "dev", tun_name],
        capture_output=True,
    )
    log.info("TUN IP updated to %s", new_ip)


def teardown_tun(tun_name: str):
    """Remove VPN subnet route."""
    subprocess.run(
        ["ip", "route", "del", TUN_SUBNET, "dev", tun_name],
        capture_output=True,
    )

# ── VPN connection ────────────────────────────────────────────────────

async def run_vpn(cfg: dict):
    """Main VPN loop with auto-reconnect."""
    import websockets

    server = cfg["server"]
    port = cfg.get("port", 443)
    username = cfg["username"]
    password = cfg["password"]
    allow_self_signed = cfg.get("allowSelfSigned", False)

    server_ip = resolve_server(server)
    log.info("Server %s resolved to %s", server, server_ip)

    tun = None
    tun_configured = False
    current_ip = None
    running = True

    loop = asyncio.get_running_loop()

    # Graceful shutdown
    def on_signal():
        nonlocal running
        running = False
        log.info("Shutting down...")

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_signal)

    try:
        while running:
            try:
                log.info("Connecting to wss://%s:%s...", server, port)

                ssl_ctx = ssl.create_default_context()
                if allow_self_signed:
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = ssl.CERT_NONE

                ws = await websockets.connect(
                    f"wss://{server}:{port}",
                    ssl=ssl_ctx,
                    max_size=2**16,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=5,
                )

                log.info("Authenticating as '%s'...", username)
                await ws.send(f"{username}:{password}")

                response = await asyncio.wait_for(ws.recv(), timeout=10)
                if isinstance(response, bytes):
                    response = response.decode()
                assigned_ip = response.strip()

                hostname = cfg.get("hostname") or socket.gethostname()
                await ws.send(f"HOST:{hostname}")
                log.info("Connected! Assigned IP: %s", assigned_ip)

                # Setup TUN on first connect
                if not tun_configured:
                    tun = TunDevice(name=TUN_NAME)
                    tun.open()
                    setup_tun(TUN_NAME, assigned_ip)
                    tun_configured = True
                    current_ip = assigned_ip
                elif assigned_ip != current_ip:
                    update_tun_ip(TUN_NAME, assigned_ip)
                    current_ip = assigned_ip

                log.info("VPN active. VPN subnet %s is accessible. Press Ctrl+C to disconnect.", TUN_SUBNET)

                # Packet loop
                send_q = asyncio.Queue(maxsize=2048)

                async def sender():
                    try:
                        while True:
                            data = await send_q.get()
                            if data is None:
                                break
                            await ws.send(data)
                    except Exception:
                        pass

                async def tun_to_ws():
                    while running:
                        try:
                            data = await loop.run_in_executor(None, tun.read, TUN_MTU + 100)
                            if data:
                                try:
                                    send_q.put_nowait(data)
                                except asyncio.QueueFull:
                                    try:
                                        send_q.get_nowait()
                                    except asyncio.QueueEmpty:
                                        pass
                                    try:
                                        send_q.put_nowait(data)
                                    except asyncio.QueueFull:
                                        pass
                        except OSError:
                            if running:
                                await asyncio.sleep(0.1)

                async def ws_to_tun():
                    try:
                        async for msg in ws:
                            if isinstance(msg, str):
                                if msg.startswith("PEERS:"):
                                    try:
                                        peers = json.loads(msg[6:])
                                        log.info("Peers updated: %s", [p.get("username", "?") for p in peers])
                                    except json.JSONDecodeError:
                                        pass
                                continue
                            if isinstance(msg, bytes) and len(msg) > 0:
                                await loop.run_in_executor(None, tun.write, msg)
                    except Exception:
                        pass

                sender_task = asyncio.create_task(sender())
                try:
                    await asyncio.gather(tun_to_ws(), ws_to_tun())
                finally:
                    try:
                        send_q.put_nowait(None)
                    except asyncio.QueueFull:
                        sender_task.cancel()
                    try:
                        await sender_task
                    except asyncio.CancelledError:
                        pass

            except (ConnectionError, OSError, ValueError) as e:
                if not running:
                    break
                log.warning("Connection lost: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3)
            except Exception as e:
                if not running:
                    break
                log.warning("Connection lost: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3)

    finally:
        # Cleanup
        if tun_configured:
            teardown_tun(TUN_NAME)
        if tun:
            tun.close()
        log.info("Disconnected. Cleanup complete.")

# ── Entry point ───────────────────────────────────────────────────────

def main():
    if os.geteuid() != 0:
        log.error("Must run as root (sudo). TUN device requires privileges.")
        sys.exit(1)

    cfg = load_config()
    log.info("VibeVPN CLI starting...")
    log.info("Server: %s:%s, User: %s", cfg["server"], cfg.get("port", 443), cfg["username"])
    log.info("Hostname: %s", cfg.get("hostname") or socket.gethostname())
    log.info("Mode: local access only (VPN subnet %s, no full traffic proxy)", TUN_SUBNET)

    try:
        asyncio.run(run_vpn(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
