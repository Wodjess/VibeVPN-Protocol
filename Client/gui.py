#!/usr/bin/env python3
"""
HTTPS VPN — macOS GUI Application.

Usage:
    python3 gui.py          (will prompt for admin password)
    sudo python3 gui.py     (direct)
"""

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from client import VPNClient, setup_routes, setup_dns, teardown_routes, restore_dns, make_ssl_context

# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------
CONFIG_DIR = Path.home() / ".config" / "https-vpn"
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULT_CONFIG = {
    "server": "",
    "port": 443,
    "secret": "",
    "dns": "1.1.1.1",
}


def load_config() -> dict:
    try:
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_FILE, 0o600)


# ---------------------------------------------------------------------------
# Logging bridge
# ---------------------------------------------------------------------------
class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


# ---------------------------------------------------------------------------
# VPN worker
# ---------------------------------------------------------------------------
class VPNWorker:
    def __init__(self, log_queue: queue.Queue, on_state_change):
        self.log_queue = log_queue
        self.on_state_change = on_state_change
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: VPNClient | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, server: str, port: int, secret: str, dns: str, insecure: bool = True):
        if self.is_running:
            return
        self._thread = threading.Thread(
            target=self._run, args=(server, port, secret, dns, insecure), daemon=True
        )
        self._thread.start()

    def stop(self):
        if self._client:
            self._client.running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self, server: str, port: int, secret: str, dns: str, insecure: bool = True):
        self.on_state_change("connecting")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._client = VPNClient(server, port, secret, insecure=insecure)

            async def vpn_loop():
                self._client.running = True
                tun_configured = False
                current_ip = None
                import websockets

                try:
                    while self._client.running:
                        try:
                            assigned_ip = await self._client.connect()
                            self.on_state_change("connected")

                            if not tun_configured:
                                self._client.create_tun(assigned_ip)
                                setup_routes(
                                    self._client.server_ip,
                                    self._client.gateway,
                                    self._client.gateway_iface,
                                    self._client.tun_name,
                                )
                                setup_dns(dns)
                                tun_configured = True
                                current_ip = assigned_ip
                            elif assigned_ip != current_ip:
                                self._client.reconfigure_tun_ip(assigned_ip)
                                current_ip = assigned_ip

                            await asyncio.gather(
                                self._client.tun_to_ws(),
                                self._client.ws_to_tun(),
                            )
                        except (
                            ConnectionError,
                            websockets.ConnectionClosed,
                            websockets.WebSocketException,
                            OSError,
                            ValueError,
                        ) as e:
                            if not self._client.running:
                                break
                            self.on_state_change("reconnecting")
                            logging.getLogger(__name__).warning(
                                "Connection lost: %s. Reconnecting in 3s...", e
                            )
                            await asyncio.sleep(3)
                finally:
                    self._client.cleanup()

            self._loop.run_until_complete(vpn_loop())
        except Exception as e:
            logging.getLogger(__name__).error("VPN error: %s", e)
        finally:
            if self._client:
                self._client.cleanup()
            self.on_state_change("disconnected")
            self._loop.close()
            self._loop = None
            self._client = None


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class VPNApp:
    BG = "#1e1e2e"
    BG_FIELD = "#313244"
    FG = "#cdd6f4"
    FG_DIM = "#6c7086"
    ACCENT = "#89b4fa"
    GREEN = "#a6e3a1"
    RED = "#f38ba8"
    YELLOW = "#f9e2af"
    BORDER = "#45475a"

    STATE_COLORS = {
        "disconnected": ("#f38ba8", "Disconnected"),
        "connecting": ("#f9e2af", "Connecting..."),
        "connected": ("#a6e3a1", "Connected"),
        "reconnecting": ("#f9e2af", "Reconnecting..."),
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("HTTPS VPN")
        self.root.configure(bg=self.BG)
        self.root.resizable(False, False)

        w, h = 520, 620
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.config = load_config()
        self.state = "disconnected"

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        qh = QueueHandler(self.log_queue)
        qh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        root_logger.addHandler(qh)

        self.worker = VPNWorker(self.log_queue, self._on_state_change)

        self._build_ui()
        self._poll_logs()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        pad = {"padx": 20, "pady": (0, 0)}

        # Title
        title_frame = tk.Frame(self.root, bg=self.BG)
        title_frame.pack(fill="x", pady=(20, 5), padx=20)

        tk.Label(
            title_frame, text="HTTPS VPN", font=("SF Pro Display", 20, "bold"),
            bg=self.BG, fg=self.FG,
        ).pack(side="left")

        # Status
        self.status_frame = tk.Frame(title_frame, bg=self.BG)
        self.status_frame.pack(side="right")

        self.status_dot = tk.Canvas(
            self.status_frame, width=12, height=12,
            bg=self.BG, highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 6))
        self._dot_id = self.status_dot.create_oval(2, 2, 10, 10, fill=self.RED, outline="")

        self.status_label = tk.Label(
            self.status_frame, text="Disconnected",
            font=("SF Pro Text", 12), bg=self.BG, fg=self.RED,
        )
        self.status_label.pack(side="left")

        tk.Frame(self.root, bg=self.BORDER, height=1).pack(fill="x", padx=20, pady=12)

        # Fields
        fields_frame = tk.Frame(self.root, bg=self.BG)
        fields_frame.pack(fill="x", **pad)

        self.server_var = tk.StringVar(value=self.config.get("server", ""))
        self.port_var = tk.StringVar(value=str(self.config.get("port", 443)))
        self.secret_var = tk.StringVar(value=self.config.get("secret", ""))
        self.dns_var = tk.StringVar(value=self.config.get("dns", "1.1.1.1"))

        fields = [
            ("Server", self.server_var, False),
            ("Port", self.port_var, False),
            ("Secret", self.secret_var, True),
            ("DNS", self.dns_var, False),
        ]

        for label_text, var, is_secret in fields:
            row = tk.Frame(fields_frame, bg=self.BG)
            row.pack(fill="x", pady=4)

            tk.Label(
                row, text=label_text, font=("SF Pro Text", 12),
                bg=self.BG, fg=self.FG_DIM, width=7, anchor="w",
            ).pack(side="left")

            entry = tk.Entry(
                row, textvariable=var, font=("SF Mono", 13),
                bg=self.BG_FIELD, fg=self.FG, insertbackground=self.FG,
                relief="flat", highlightthickness=1,
                highlightcolor=self.ACCENT, highlightbackground=self.BORDER,
            )
            if is_secret:
                entry.configure(show="\u2022")
            entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(4, 0))

        # Button
        btn_frame = tk.Frame(self.root, bg=self.BG)
        btn_frame.pack(fill="x", padx=20, pady=(16, 0))

        self.connect_btn = tk.Button(
            btn_frame, text="Connect", font=("SF Pro Text", 14, "bold"),
            bg=self.ACCENT, fg=self.BG, activebackground="#74c7ec",
            activeforeground=self.BG, relief="flat", cursor="hand2",
            command=self._toggle_connection,
        )
        self.connect_btn.pack(fill="x", ipady=8)

        tk.Frame(self.root, bg=self.BORDER, height=1).pack(fill="x", padx=20, pady=12)

        # Log
        tk.Label(
            self.root, text="Log", font=("SF Pro Text", 11),
            bg=self.BG, fg=self.FG_DIM, anchor="w",
        ).pack(fill="x", padx=20)

        log_frame = tk.Frame(self.root, bg=self.BG_FIELD, highlightthickness=1,
                             highlightbackground=self.BORDER)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(4, 20))

        self.log_text = tk.Text(
            log_frame, font=("SF Mono", 10), bg=self.BG_FIELD, fg=self.FG_DIM,
            relief="flat", state="disabled", wrap="word",
            highlightthickness=0, borderwidth=8,
        )
        self.log_text.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(self.log_text, orient="vertical",
                                  command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _on_state_change(self, new_state: str):
        self.state = new_state
        self.root.after(0, self._update_ui_state)

    def _update_ui_state(self):
        color, text = self.STATE_COLORS.get(self.state, (self.RED, "Unknown"))
        self.status_dot.itemconfig(self._dot_id, fill=color)
        self.status_label.configure(text=text, fg=color)

        if self.state == "connected":
            self.connect_btn.configure(text="Disconnect", bg=self.RED, activebackground="#eba0ac")
        elif self.state == "disconnected":
            self.connect_btn.configure(text="Connect", bg=self.ACCENT, activebackground="#74c7ec")
            self._set_fields_enabled(True)
        else:
            self.connect_btn.configure(text="Disconnect", bg=self.YELLOW, activebackground="#f9e2af")

    def _set_fields_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for child in self.root.winfo_children():
            for widget in child.winfo_children():
                if isinstance(widget, tk.Frame):
                    for w in widget.winfo_children():
                        if isinstance(w, tk.Entry):
                            w.configure(state=state)

    def _toggle_connection(self):
        if self.worker.is_running:
            self.worker.stop()
            return

        server = self.server_var.get().strip()
        secret = self.secret_var.get().strip()
        dns = self.dns_var.get().strip() or "1.1.1.1"

        if not server:
            self._log_message("ERROR: Server address is required")
            return
        if not secret:
            self._log_message("ERROR: Secret is required")
            return

        try:
            port = int(self.port_var.get().strip() or "443")
        except ValueError:
            self._log_message("ERROR: Port must be a number")
            return

        self.config.update(server=server, port=port, secret=secret, dns=dns)
        save_config(self.config)

        self._set_fields_enabled(False)
        self.worker.start(server, port, secret, dns)

    def _poll_logs(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self._log_message(msg)
            except queue.Empty:
                break
        self.root.after(100, self._poll_logs)

    def _log_message(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        if self.worker.is_running:
            self.worker.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def _escape_for_applescript(s: str) -> str:
    """Escape a string for safe embedding in AppleScript."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main():
    if os.geteuid() != 0:
        script_path = _escape_for_applescript(os.path.abspath(__file__))
        python_path = _escape_for_applescript(sys.executable)
        os.execvp(
            "osascript",
            [
                "osascript", "-e",
                f'do shell script "{python_path} {script_path}" '
                f'with administrator privileges',
            ],
        )

    app = VPNApp()
    app.run()


if __name__ == "__main__":
    main()
