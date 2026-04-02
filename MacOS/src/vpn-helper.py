#!/usr/bin/env python3
"""
VibeVPN Privileged Helper Daemon.

Runs as root via launchd. Accepts commands from the Electron UI
over a local Unix socket. Manages TUN, routes, DNS.

Install: sudo python3 install-helper.py
"""

import asyncio
import json
import logging
import os
import platform
import signal
import socket
import struct
import subprocess
import sys

# Add bundled modules to path
HELPER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HELPER_DIR)

import websockets
from common import SERVER_TUN_IP, TUN_MTU
from tun_darwin import TunDevice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HELPER] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/vibevpn-helper.log"),
    ],
)
log = logging.getLogger(__name__)

SOCKET_PATH = "/tmp/vibevpn.sock"
STATE_FILE = "/tmp/vibevpn-state.json"

# ── VPN State ──────────────────────────────────────────────────────────

class VPNState:
    MAX_LOGS = 50

    def __init__(self):
        self.connected = False
        self.server = None
        self.port = None
        self.username = None
        self.password = None
        self.insecure = False
        self.assigned_ip = None
        self.tun = None
        self.tun_name = None
        self.ws = None
        self.running = False
        self.gateway = None
        self.gateway_iface = None
        self.server_ip = None
        self._logs = []
        self._task = None
        self._peers = []

    def add_log(self, msg: str):
        self._logs.append(msg)
        if len(self._logs) > self.MAX_LOGS:
            self._logs = self._logs[-self.MAX_LOGS:]

    def to_dict(self):
        return {
            "connected": self.connected,
            "server": self.server,
            "assigned_ip": self.assigned_ip,
            "peers": self._peers,
            "logs": self._logs,
        }

    def save_last_connection(self):
        """Save connection params for auto-reconnect on reboot."""
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "server": self.server,
                    "port": self.port,
                    "username": self.username,
                    "password": self.password,
                    "insecure": self.insecure,
                }, f)
            os.chmod(STATE_FILE, 0o600)
        except OSError:
            pass

    @staticmethod
    def load_last_connection():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    @staticmethod
    def clear_last_connection():
        try:
            os.unlink(STATE_FILE)
        except OSError:
            pass


vpn = VPNState()

# ── Networking helpers ─────────────────────────────────────────────────

def resolve_server(hostname):
    return socket.gethostbyname(hostname)

def get_default_gateway():
    result = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True)
    gw, iface = None, None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("gateway:"): gw = line.split(":")[1].strip()
        if line.startswith("interface:"): iface = line.split(":")[1].strip()
    return gw or "192.168.1.1", iface or "en0"

def setup_routes(server_ip, gateway, tun_name):
    subprocess.run(["route", "add", "-host", server_ip, gateway], capture_output=True)
    subprocess.run(["route", "add", "-net", "0.0.0.0/1", "-interface", tun_name], capture_output=True)
    subprocess.run(["route", "add", "-net", "128.0.0.0/1", "-interface", tun_name], capture_output=True)
    subprocess.run(["route", "add", "-inet6", "-net", "::/1", "-blackhole"], capture_output=True)
    subprocess.run(["route", "add", "-inet6", "-net", "8000::/1", "-blackhole"], capture_output=True)

def teardown_routes(server_ip):
    for cmd in [
        ["route", "delete", "-net", "0.0.0.0/1"],
        ["route", "delete", "-net", "128.0.0.0/1"],
        ["route", "delete", "-host", server_ip],
        ["route", "delete", "-inet6", "-net", "::/1"],
        ["route", "delete", "-inet6", "-net", "8000::/1"],
    ]:
        subprocess.run(cmd, capture_output=True)

def setup_dns(dns="1.1.1.1"):
    result = subprocess.run(["networksetup", "-listallnetworkservices"], capture_output=True, text=True)
    for svc in result.stdout.splitlines()[1:]:
        svc = svc.strip()
        if svc and not svc.startswith("*"):
            subprocess.run(["networksetup", "-setdnsservers", svc, dns], capture_output=True)

def restore_dns():
    result = subprocess.run(["networksetup", "-listallnetworkservices"], capture_output=True, text=True)
    for svc in result.stdout.splitlines()[1:]:
        svc = svc.strip()
        if svc and not svc.startswith("*"):
            subprocess.run(["networksetup", "-setdnsservers", svc, "empty"], capture_output=True)

# ── VPN tunnel logic ──────────────────────────────────────────────────

import ssl

async def vpn_connect(server, port, username, password, insecure=False):
    """Connect to VPN server. Runs as a long-lived task."""
    global vpn
    vpn.server = server
    vpn.port = port
    vpn.username = username
    vpn.password = password
    vpn.insecure = insecure
    vpn.running = True

    vpn._logs = []
    vpn.add_log(f"Resolving {server}...")
    vpn.server_ip = resolve_server(server)
    vpn.gateway, vpn.gateway_iface = get_default_gateway()
    vpn.add_log(f"Server IP: {vpn.server_ip}, gateway: {vpn.gateway}")

    tun_configured = False
    current_ip = None

    while vpn.running:
        try:
            vpn.add_log(f"Connecting to wss://{server}:{port}...")

            ssl_ctx = ssl.create_default_context()
            if insecure:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            vpn.ws = await websockets.connect(
                f"wss://{server}:{port}",
                ssl=ssl_ctx, max_size=2**16,
                ping_interval=20, ping_timeout=60, close_timeout=5,
            )

            vpn.add_log(f"Authenticating as '{username}'...")
            await vpn.ws.send(f"{username}:{password}")

            response = await asyncio.wait_for(vpn.ws.recv(), timeout=10)
            if isinstance(response, bytes): response = response.decode()
            assigned_ip = response.strip()
            vpn.assigned_ip = assigned_ip
            vpn.connected = True

            await vpn.ws.send(f"HOST:{socket.gethostname()}")

            vpn.add_log(f"Connected, assigned IP: {assigned_ip}")
            log.info("Connected, assigned IP: %s", assigned_ip)

            if not tun_configured:
                # Create TUN
                for unit in range(5, 20):
                    try:
                        vpn.tun = TunDevice(unit=unit)
                        vpn.tun.open()
                        vpn.tun_name = f"utun{unit}"
                        break
                    except OSError:
                        continue
                else:
                    raise RuntimeError("Could not open utun device")

                vpn.add_log(f"TUN {vpn.tun_name} opened")
                subprocess.run(
                    ["ifconfig", vpn.tun_name, assigned_ip, SERVER_TUN_IP, "mtu", str(TUN_MTU), "up"],
                    check=True,
                )
                setup_routes(vpn.server_ip, vpn.gateway, vpn.tun_name)
                setup_dns()
                vpn.add_log(f"Routes configured, DNS set to 1.1.1.1")
                tun_configured = True
                current_ip = assigned_ip
                vpn.save_last_connection()
            elif assigned_ip != current_ip:
                subprocess.run(["ifconfig", vpn.tun_name, assigned_ip, SERVER_TUN_IP], check=True)
                current_ip = assigned_ip

            # Run packet loop
            send_q = asyncio.Queue(maxsize=2048)
            loop = asyncio.get_running_loop()

            async def sender():
                try:
                    while True:
                        data = await send_q.get()
                        if data is None: break
                        await vpn.ws.send(data)
                except websockets.ConnectionClosed:
                    pass

            async def tun_to_ws():
                while vpn.running:
                    try:
                        data = await loop.run_in_executor(None, vpn.tun.read, TUN_MTU + 100)
                        if data:
                            try: send_q.put_nowait(data)
                            except asyncio.QueueFull:
                                try: send_q.get_nowait()
                                except: pass
                                try: send_q.put_nowait(data)
                                except: pass
                    except OSError:
                        if vpn.running: await asyncio.sleep(0.1)

            async def ws_to_tun():
                try:
                    async for msg in vpn.ws:
                        if isinstance(msg, str):
                            if msg.startswith("PEERS:"):
                                try: vpn._peers = json.loads(msg[6:])
                                except: pass
                            continue
                        if isinstance(msg, bytes) and len(msg) > 0:
                            await loop.run_in_executor(None, vpn.tun.write, msg)
                except websockets.ConnectionClosed:
                    pass

            sender_task = asyncio.create_task(sender())
            await asyncio.gather(tun_to_ws(), ws_to_tun())
            try: send_q.put_nowait(None)
            except: sender_task.cancel()
            try: await sender_task
            except: pass

        except (ConnectionError, websockets.ConnectionClosed, websockets.WebSocketException, OSError, ValueError) as e:
            if not vpn.running: break
            vpn.connected = False
            vpn.add_log(f"Connection lost: {e}. Reconnecting in 3s...")
            log.warning("Connection lost: %s. Reconnecting in 3s...", e)
            await asyncio.sleep(3)

    # Cleanup
    vpn.connected = False
    if vpn.server_ip:
        teardown_routes(vpn.server_ip)
    if vpn.tun:
        vpn.tun.close()
        vpn.tun = None
    restore_dns()
    log.info("VPN disconnected and cleaned up")


async def vpn_disconnect():
    global vpn
    vpn.running = False
    if vpn.ws:
        try: await vpn.ws.close()
        except: pass
    vpn.clear_last_connection()


# ── Unix socket server (UI ↔ helper communication) ────────────────────

async def handle_ui_client(reader, writer):
    """Handle commands from Electron UI over Unix socket."""
    try:
        while True:
            length_data = await reader.readexactly(4)
            length = struct.unpack("!I", length_data)[0]
            data = await reader.readexactly(length)
            cmd = json.loads(data.decode())

            action = cmd.get("action")
            response = {}

            if action == "status":
                response = vpn.to_dict()

            elif action == "connect":
                if vpn.connected or vpn.running:
                    response = {"error": "Already connected"}
                else:
                    is_ip = all(c.isdigit() or c == '.' for c in cmd["server"])
                    vpn._task = asyncio.create_task(vpn_connect(
                        cmd["server"], cmd.get("port", 443),
                        cmd["username"], cmd["password"],
                        insecure=is_ip,
                    ))
                    # Wait for connection (up to 15 seconds)
                    for _ in range(150):
                        await asyncio.sleep(0.1)
                        if vpn.connected: break
                    response = vpn.to_dict()

            elif action == "disconnect":
                await vpn_disconnect()
                if vpn._task:
                    try: await asyncio.wait_for(vpn._task, timeout=5)
                    except: pass
                response = {"connected": False}

            elif action == "peers":
                response = {"peers": vpn._peers}

            # Send response
            resp_data = json.dumps(response).encode()
            writer.write(struct.pack("!I", len(resp_data)) + resp_data)
            await writer.drain()

    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def main():
    # Remove stale socket
    try: os.unlink(SOCKET_PATH)
    except OSError: pass

    server = await asyncio.start_unix_server(handle_ui_client, path=SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)  # Allow non-root UI to connect

    log.info("Helper daemon listening on %s", SOCKET_PATH)

    # Auto-reconnect last connection on startup
    last = VPNState.load_last_connection()
    if last:
        log.info("Auto-reconnecting to %s as %s", last["server"], last["username"])
        is_ip = all(c.isdigit() or c == '.' for c in last["server"])
        vpn._task = asyncio.create_task(vpn_connect(
            last["server"], last.get("port", 443),
            last["username"], last["password"],
            insecure=is_ip,
        ))

    # Handle signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(server)))

    await server.serve_forever()


async def shutdown(server):
    log.info("Shutting down...")
    await vpn_disconnect()
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
