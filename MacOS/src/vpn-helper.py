#!/usr/bin/env python3
"""
VibeVPN Privileged Helper Daemon.

Runs as root via launchd. Accepts commands from the Electron UI
over a local Unix socket. Manages TUN, routes, DNS.
"""

import asyncio
import ipaddress
import json
import logging
import os
import signal
import socket
import ssl
import struct
import subprocess
import sys
from collections import deque
from logging.handlers import RotatingFileHandler

# Add bundled modules to path
HELPER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HELPER_DIR)

import websockets
from common import SERVER_TUN_IP, TUN_MTU
from tun_darwin import TunDevice

# ── Paths ─────────────────────────────────────────────────────────────

SOCKET_PATH = "/tmp/vibevpn.sock"

STATE_DIR = "/Library/Application Support/VibeVPN"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

LOG_DIR = "/Library/Logs/VibeVPN"
LOG_FILE = os.path.join(LOG_DIR, "helper.log")

for _d in (STATE_DIR, LOG_DIR):
    os.makedirs(_d, mode=0o700, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HELPER] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3),
    ],
)
log = logging.getLogger(__name__)

# Connection lock — serializes connect/disconnect operations
_conn_lock = asyncio.Lock()

# Reconnect backoff
BACKOFF_BASE = 3
BACKOFF_MAX = 60

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
        self._logs = deque(maxlen=self.MAX_LOGS)
        self._task = None
        self._peers = []

    def add_log(self, msg: str):
        self._logs.append(msg)

    def to_dict(self):
        return {
            "connected": self.connected,
            "server": self.server,
            "assigned_ip": self.assigned_ip,
            "peers": self._peers,
            "logs": list(self._logs),
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

# ── Helpers ────────────────────────────────────────────────────────────

def is_ip_address(s: str) -> bool:
    """Check if string is a valid IP address (not just digits and dots)."""
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def validate_assigned_ip(ip_str: str) -> bool:
    """Validate that server returned a valid IPv4 address."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return isinstance(addr, ipaddress.IPv4Address)
    except ValueError:
        return False


def resolve_server(hostname: str) -> str:
    return socket.gethostbyname(hostname)


def get_default_gateway():
    result = subprocess.run(
        ["route", "-n", "get", "default"], capture_output=True, text=True
    )
    gw, iface = None, None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("gateway:"):
            gw = line.split(":", 1)[1].strip()
        elif line.startswith("interface:"):
            iface = line.split(":", 1)[1].strip()
    if not gw or not iface:
        raise RuntimeError(
            f"Cannot determine default gateway (gw={gw}, iface={iface})"
        )
    return gw, iface


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
    result = subprocess.run(
        ["networksetup", "-listallnetworkservices"], capture_output=True, text=True
    )
    for svc in result.stdout.splitlines()[1:]:
        svc = svc.strip()
        if svc and not svc.startswith("*"):
            subprocess.run(
                ["networksetup", "-setdnsservers", svc, dns], capture_output=True
            )


def restore_dns():
    result = subprocess.run(
        ["networksetup", "-listallnetworkservices"], capture_output=True, text=True
    )
    for svc in result.stdout.splitlines()[1:]:
        svc = svc.strip()
        if svc and not svc.startswith("*"):
            subprocess.run(
                ["networksetup", "-setdnsservers", svc, "empty"], capture_output=True
            )


# ── VPN tunnel logic ──────────────────────────────────────────────────

async def vpn_connect(server, port, username, password, insecure=False):
    """Connect to VPN server. Runs as a long-lived task."""
    global vpn
    vpn.server = server
    vpn.port = port
    vpn.username = username
    vpn.password = password
    vpn.insecure = insecure
    vpn.running = True
    vpn._logs = deque(maxlen=VPNState.MAX_LOGS)

    tun_configured = False
    current_ip = None
    backoff = BACKOFF_BASE

    while vpn.running:
        try:
            # Resolve inside loop so DNS/network failures are retried
            if not vpn.server_ip:
                vpn.add_log(f"Resolving {server}...")
                vpn.server_ip = resolve_server(server)
            if not vpn.gateway:
                vpn.gateway, vpn.gateway_iface = get_default_gateway()
                vpn.add_log(f"Server IP: {vpn.server_ip}, gateway: {vpn.gateway}")

            vpn.add_log(f"Connecting to wss://{server}:{port}...")

            ssl_ctx = ssl.create_default_context()
            if insecure:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            vpn.ws = await websockets.connect(
                f"wss://{server}:{port}",
                ssl=ssl_ctx, max_size=2**16,
                ping_interval=20, ping_timeout=60, close_timeout=5,
                open_timeout=10,
            )

            vpn.add_log(f"Authenticating as '{username}'...")
            await vpn.ws.send(f"{username}:{password}")

            response = await asyncio.wait_for(vpn.ws.recv(), timeout=10)
            if isinstance(response, bytes):
                response = response.decode()
            assigned_ip = response.strip()

            if not validate_assigned_ip(assigned_ip):
                raise ValueError(f"Server returned invalid IP: {assigned_ip!r}")

            vpn.assigned_ip = assigned_ip
            vpn.connected = True
            backoff = BACKOFF_BASE  # Reset on success

            await vpn.ws.send(f"HOST:{socket.gethostname()}")
            vpn.add_log(f"Connected, assigned IP: {assigned_ip}")
            log.info("Connected, assigned IP: %s", assigned_ip)

            if not tun_configured:
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
                vpn.tun.sock.settimeout(1.0)  # Allow cancellation of blocking reads
                subprocess.run(
                    ["ifconfig", vpn.tun_name, assigned_ip, SERVER_TUN_IP,
                     "mtu", str(TUN_MTU), "up"],
                    check=True,
                )
                setup_routes(vpn.server_ip, vpn.gateway, vpn.tun_name)
                setup_dns()
                vpn.add_log("Routes configured, DNS set to 1.1.1.1")
                tun_configured = True
                current_ip = assigned_ip
                vpn.save_last_connection()
            elif assigned_ip != current_ip:
                subprocess.run(
                    ["ifconfig", vpn.tun_name, assigned_ip, SERVER_TUN_IP],
                    check=True,
                )
                current_ip = assigned_ip

            # ── Packet loop ───────────────────────────────────────
            send_q = asyncio.Queue(maxsize=2048)
            loop = asyncio.get_running_loop()

            async def sender():
                try:
                    while True:
                        data = await send_q.get()
                        if data is None:
                            break
                        await vpn.ws.send(data)
                except websockets.ConnectionClosed:
                    pass

            async def tun_to_ws():
                while vpn.running:
                    try:
                        data = await loop.run_in_executor(
                            None, vpn.tun.read, TUN_MTU + 100
                        )
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
                        if vpn.running:
                            await asyncio.sleep(0.1)

            async def ws_to_tun():
                try:
                    async for msg in vpn.ws:
                        if isinstance(msg, str):
                            if msg.startswith("PEERS:"):
                                try:
                                    vpn._peers = json.loads(msg[6:])
                                except json.JSONDecodeError:
                                    pass
                            continue
                        if isinstance(msg, bytes) and len(msg) > 0:
                            await loop.run_in_executor(None, vpn.tun.write, msg)
                except websockets.ConnectionClosed:
                    pass

            sender_task = asyncio.create_task(sender())
            t_tun = asyncio.create_task(tun_to_ws())
            t_ws = asyncio.create_task(ws_to_tun())
            # Wait for FIRST task to finish (e.g. websocket dies),
            # then cancel the other so reconnect loop can proceed
            done, pending = await asyncio.wait(
                [t_tun, t_ws], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                send_q.put_nowait(None)
            except asyncio.QueueFull:
                sender_task.cancel()
            try:
                await sender_task
            except (asyncio.CancelledError, Exception):
                pass

        except (ConnectionError, websockets.ConnectionClosed,
                websockets.WebSocketException, OSError, ValueError,
                RuntimeError, socket.gaierror) as e:
            if not vpn.running:
                break
            vpn.connected = False
            vpn.add_log(f"Connection lost: {e}. Reconnecting in {backoff}s...")
            log.warning("Connection lost: %s. Reconnecting in %ds...", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    # ── Cleanup (each step independent so failures don't cascade) ──
    vpn.connected = False
    try:
        if vpn.server_ip:
            teardown_routes(vpn.server_ip)
    except Exception as e:
        log.error("Failed to teardown routes: %s", e)
    try:
        if vpn.tun:
            vpn.tun.close()
            vpn.tun = None
    except Exception as e:
        log.error("Failed to close TUN: %s", e)
    try:
        restore_dns()
    except Exception as e:
        log.error("Failed to restore DNS: %s", e)
    log.info("VPN disconnected and cleaned up")


async def vpn_disconnect():
    global vpn
    vpn.running = False
    if vpn.ws:
        try:
            await vpn.ws.close()
        except Exception:
            pass
    vpn.clear_last_connection()


# ── Unix socket server ────────────────────────────────────────────────

async def handle_ui_client(reader, writer):
    """Handle commands from Electron UI over Unix socket."""
    try:
        while True:
            # Timeout prevents slow-client DoS
            length_data = await asyncio.wait_for(
                reader.readexactly(4), timeout=30
            )
            length = struct.unpack("!I", length_data)[0]
            if length > 1_000_000:  # Max 1MB message
                break
            data = await asyncio.wait_for(
                reader.readexactly(length), timeout=30
            )
            cmd = json.loads(data.decode())

            action = cmd.get("action")
            response = {}

            if action == "status":
                response = vpn.to_dict()

            elif action == "connect":
                async with _conn_lock:
                    # If already connected, disconnect first (server switch)
                    if vpn.running or vpn.connected:
                        vpn.add_log("Switching server...")
                        await vpn_disconnect()
                        if vpn._task:
                            try:
                                await asyncio.wait_for(vpn._task, timeout=10)
                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                vpn._task.cancel()
                        # Force cleanup leftover state
                        if vpn.tun:
                            try:
                                vpn.tun.close()
                            except Exception:
                                pass
                            vpn.tun = None
                        if vpn.server_ip:
                            try:
                                teardown_routes(vpn.server_ip)
                            except Exception:
                                pass
                        try:
                            restore_dns()
                        except Exception:
                            pass
                        vpn.connected = False
                        vpn.running = False
                        vpn.server_ip = None
                        vpn.gateway = None
                        vpn._peers = []
                        vpn._task = None

                    srv = cmd.get("server", "")
                    vpn._task = asyncio.create_task(vpn_connect(
                        srv, cmd.get("port", 443),
                        cmd.get("username", ""), cmd.get("password", ""),
                        insecure=is_ip_address(srv),
                    ))
                    # Wait for connection (up to 15s)
                    for _ in range(150):
                        await asyncio.sleep(0.1)
                        if vpn.connected:
                            break
                    response = vpn.to_dict()

            elif action == "disconnect":
                async with _conn_lock:
                    await vpn_disconnect()
                    if vpn._task:
                        try:
                            await asyncio.wait_for(vpn._task, timeout=10)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            vpn._task.cancel()
                    response = {"connected": False}

            elif action == "peers":
                response = {"peers": vpn._peers}

            # Send response
            resp_data = json.dumps(response).encode()
            writer.write(struct.pack("!I", len(resp_data)) + resp_data)
            await writer.drain()

    except (asyncio.IncompleteReadError, ConnectionResetError,
            BrokenPipeError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()


async def main():
    # Remove stale socket
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass

    server = await asyncio.start_unix_server(handle_ui_client, path=SOCKET_PATH)
    # 0o666: non-root UI process must be able to connect
    os.chmod(SOCKET_PATH, 0o666)

    log.info("Helper daemon listening on %s", SOCKET_PATH)

    # Auto-reconnect last connection on startup
    last = VPNState.load_last_connection()
    if last:
        log.info("Auto-reconnecting to %s as %s", last["server"], last["username"])
        srv = last["server"]
        vpn._task = asyncio.create_task(vpn_connect(
            srv, last.get("port", 443),
            last["username"], last["password"],
            insecure=is_ip_address(srv),
        ))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(server)))

    await server.serve_forever()


async def shutdown(server):
    log.info("Shutting down...")
    await vpn_disconnect()
    if vpn._task:
        try:
            await asyncio.wait_for(vpn._task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            vpn._task.cancel()
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
