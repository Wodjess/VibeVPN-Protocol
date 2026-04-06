/**
 * VibeVPN Windows — VPN service logic running in Electron main process.
 * Uses koffi (FFI) to call wintun.dll, ws for WebSocket tunnel.
 * macOS is NOT affected — this module is only loaded on Windows.
 */

const path = require('node:path');
const { execSync, execFileSync } = require('node:child_process');
const { existsSync } = require('node:fs');
const { app } = require('electron');
const WebSocket = require('ws');

function isValidIp(s) {
  if (!/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(s)) return false;
  return s.split('.').every(n => { const v = parseInt(n, 10); return v >= 0 && v <= 255; });
}

const _logs = [];
function log(...args) {
  const msg = args.join(' ');
  console.log('[VPN-WIN]', msg);
  _logs.push(msg);
  if (_logs.length > 100) _logs.shift();
}

// Reconnect backoff
const BACKOFF_BASE = 3;
const BACKOFF_MAX = 60;

// Connection lock — prevents concurrent connect/disconnect
let _connLock = Promise.resolve();

// ── State ────────────────────────────────────────────────────────────────

const state = {
  connected: false,
  running: false,
  server: null,
  port: null,
  assignedIp: null,
  peers: [],
  error: null,
  _ws: null,
  _tun: null,
  _readInterval: null,
};

// ── Wintun via koffi (lazy-loaded) ───────────────────────────────────────

let koffi = null;
let wintunLib = null;
let kernel32 = null;
let wt = {};

function loadWintun() {
  if (wintunLib) return;

  koffi = require('koffi');

  // Find wintun.dll
  const dllPaths = [
    path.join(process.resourcesPath || '', 'wintun.dll'),
    path.join(process.resourcesPath || '', 'service', 'wintun.dll'),
    path.join(__dirname, '..', '..', '..', 'Windows', 'wintun.dll'),
  ];
  let dllPath = dllPaths.find(p => existsSync(p));
  if (!dllPath) throw new Error('wintun.dll not found in: ' + dllPaths.join(', '));

  log('Loading wintun.dll from:', dllPath);
  wintunLib = koffi.load(dllPath);
  kernel32 = koffi.load('kernel32.dll');

  // Define WinTUN functions
  wt.CreateAdapter = wintunLib.func('void* __stdcall WintunCreateAdapter(const char16_t* Name, const char16_t* TunnelType, void* RequestedGUID)');
  wt.CloseAdapter = wintunLib.func('void __stdcall WintunCloseAdapter(void* Adapter)');
  wt.StartSession = wintunLib.func('void* __stdcall WintunStartSession(void* Adapter, uint32_t Capacity)');
  wt.EndSession = wintunLib.func('void __stdcall WintunEndSession(void* Session)');
  wt.GetReadWaitEvent = wintunLib.func('void* __stdcall WintunGetReadWaitEvent(void* Session)');
  wt.ReceivePacket = wintunLib.func('void* __stdcall WintunReceivePacket(void* Session, _Out_ uint32_t* PacketSize)');
  wt.ReleaseReceivePacket = wintunLib.func('void __stdcall WintunReleaseReceivePacket(void* Session, void* Packet)');
  wt.AllocateSendPacket = wintunLib.func('void* __stdcall WintunAllocateSendPacket(void* Session, uint32_t PacketSize)');
  wt.SendPacket = wintunLib.func('void __stdcall WintunSendPacket(void* Session, void* Packet)');
  wt.WaitForSingleObject = kernel32.func('uint32_t __stdcall WaitForSingleObject(void* hHandle, uint32_t dwMilliseconds)');
  wt.GetLastError = kernel32.func('uint32_t __stdcall GetLastError()');

  log('wintun.dll loaded successfully');
}

// ── TUN management ───────────────────────────────────────────────────────

let tunAdapter = null;
let tunSession = null;
let tunReadEvent = null;

function tunOpen(name) {
  loadWintun();
  log('Creating TUN adapter:', name);
  tunAdapter = wt.CreateAdapter(name, name, null);
  if (!tunAdapter) {
    const err = wt.GetLastError();
    // Error 5 = ERROR_ACCESS_DENIED (no admin), 577 = ERROR_INVALID_IMAGE_HASH
    const errMsg = err === 5 ? 'Access denied — run as Administrator'
                 : err === 577 ? 'Driver signature error — disable Secure Boot or use test signing'
                 : `WintunCreateAdapter failed (error ${err})`;
    throw new Error(errMsg);
  }
  log('TUN adapter created OK, starting session...');
  tunSession = wt.StartSession(tunAdapter, 0x400000);
  if (!tunSession) {
    const err = wt.GetLastError();
    wt.CloseAdapter(tunAdapter);
    tunAdapter = null;
    throw new Error(`WintunStartSession failed (error ${err})`);
  }
  tunReadEvent = wt.GetReadWaitEvent(tunSession);
  log('TUN session started OK');
}

function tunConfigure(ip, gateway, mtu) {
  if (!isValidIp(ip) || !isValidIp(gateway)) throw new Error(`Invalid IP: ${ip} / ${gateway}`);
  log('Configuring TUN:', ip, 'gw', gateway, 'mtu', mtu);
  try {
    execFileSync('netsh', ['interface', 'ip', 'set', 'address', 'name=VibeVPN', 'static', ip, '255.255.255.0', gateway],
      { windowsHide: true, timeout: 15000 });
    log('netsh set address OK');
  } catch (e) {
    log('netsh set address FAILED:', e.message);
  }
  try {
    execFileSync('netsh', ['interface', 'ipv4', 'set', 'subinterface', 'VibeVPN', `mtu=${mtu}`, 'store=persistent'],
      { windowsHide: true, timeout: 10000 });
  } catch {}
}

function tunRead() {
  if (!tunSession) return null;
  const sizeOut = [0];
  const ptr = wt.ReceivePacket(tunSession, sizeOut);
  if (!ptr) return null;
  const size = sizeOut[0];
  if (size === 0 || size > 65535) {
    wt.ReleaseReceivePacket(tunSession, ptr);
    return null;
  }
  // koffi.array('uint8_t', N) creates a sized array type for decode
  const arrType = koffi.array('uint8_t', size);
  const arr = koffi.decode(ptr, arrType);
  wt.ReleaseReceivePacket(tunSession, ptr);
  return Buffer.from(arr);
}

function tunWrite(data) {
  if (!tunSession || !data || data.length === 0) return;
  const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
  const ptr = wt.AllocateSendPacket(tunSession, buf.length);
  if (!ptr) return;
  // koffi.array('uint8_t', N) + encode copies Buffer to native memory
  const arrType = koffi.array('uint8_t', buf.length);
  koffi.encode(ptr, arrType, buf);
  wt.SendPacket(tunSession, ptr);
}

function tunClose() {
  if (tunSession) { try { wt.EndSession(tunSession); } catch {} tunSession = null; }
  if (tunAdapter) { try { wt.CloseAdapter(tunAdapter); } catch {} tunAdapter = null; }
  tunReadEvent = null;
}

// ── Route & DNS ──────────────────────────────────────────────────────────

function getDefaultGateway() {
  try {
    const out = execSync(
      "powershell -NoProfile -Command \"(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).NextHop\"",
      { windowsHide: true, timeout: 15000, encoding: 'utf8' }
    );
    const gw = out.trim();
    if (!gw || !isValidIp(gw)) {
      throw new Error(`Invalid gateway: ${gw}`);
    }
    log('Default gateway:', gw);
    return gw;
  } catch (e) {
    throw new Error(`Cannot determine default gateway: ${e.message}`);
  }
}

function setupRoutes(serverIp, gateway) {
  if (!isValidIp(serverIp) || !isValidIp(gateway)) return;
  log('Setting up routes: server', serverIp, 'via', gateway);
  const run = (args) => {
    try {
      execFileSync('route', args, { windowsHide: true, timeout: 5000 });
      log('  route', args.join(' '), '- OK');
    } catch (e) {
      log('  route', args.join(' '), '- FAILED:', e.message);
    }
  };
  // Route to VPN server goes through the real gateway (not the tunnel)
  run(['add', serverIp, 'mask', '255.255.255.255', gateway, 'metric', '5']);
  // All other traffic goes through the tunnel
  run(['add', '0.0.0.0', 'mask', '128.0.0.0', '10.8.0.1', 'metric', '5']);
  run(['add', '128.0.0.0', 'mask', '128.0.0.0', '10.8.0.1', 'metric', '5']);
}

function teardownRoutes(serverIp) {
  const run = (args) => { try { execFileSync('route', args, { windowsHide: true, timeout: 5000 }); } catch {} };
  run(['delete', '0.0.0.0', 'mask', '128.0.0.0']);
  run(['delete', '128.0.0.0', 'mask', '128.0.0.0']);
  if (serverIp && isValidIp(serverIp)) run(['delete', serverIp]);
}

function setupDns() {
  log('Setting DNS to 1.1.1.1...');
  try {
    execFileSync('powershell', ['-NoProfile', '-Command',
      "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -ne 'VibeVPN' } | Set-DnsClientServerAddress -ServerAddresses 1.1.1.1"],
      { windowsHide: true, timeout: 30000 }
    );
    log('DNS set OK');
  } catch (e) { log('DNS set FAILED:', e.message); }
}

function restoreDns() {
  try {
    execSync(
      "powershell -NoProfile -Command \"Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -ne 'VibeVPN' } | Set-DnsClientServerAddress -ResetServerAddresses\"",
      { windowsHide: true, timeout: 15000 }
    );
  } catch {}
}

// ── Resolve hostname ─────────────────────────────────────────────────────

function resolveHost(host) {
  const { lookup } = require('node:dns');
  return new Promise((resolve, reject) => {
    lookup(host, 4, (err, addr) => err ? reject(err) : resolve(addr));
  });
}

// ── VPN Connect ──────────────────────────────────────────────────────────

let serverIp = null;
let tunPktsSent = 0;
let tunPktsRecv = 0;

async function vpnConnect({ server, port, username, password }) {
  state.server = server;
  state.port = port || 443;
  state.running = true;
  state.error = null;
  state.connected = false;
  tunPktsSent = 0;
  tunPktsRecv = 0;

  log('Connecting to', server, ':', state.port);

  const isIp = isValidIp(server);
  let gateway = null;
  let tunConfigured = false;
  let backoff = BACKOFF_BASE;

  while (state.running) {
    try {
      // Resolve inside loop so DNS/network failures are retried
      if (!serverIp) {
        serverIp = await resolveHost(server);
        log('Resolved', server, '->', serverIp);
      }
      if (!gateway) {
        gateway = getDefaultGateway();
      }

      const wsUrl = `wss://${server}:${state.port}`;
      const wsOpts = isIp ? { rejectUnauthorized: false } : {};

      log('WebSocket connecting to', wsUrl);
      state._ws = new WebSocket(wsUrl, wsOpts);

      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Connection timeout')), 15000);
        state._ws.once('open', () => { clearTimeout(timer); resolve(); });
        state._ws.once('error', (e) => { clearTimeout(timer); reject(e); });
      });

      log('WebSocket connected, authenticating...');

      // Authenticate
      state._ws.send(`${username}:${password}`);
      const response = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Auth timeout')), 10000);
        state._ws.once('message', (data) => { clearTimeout(timer); resolve(data.toString()); });
      });

      const assignedIp = response.trim();
      if (!isValidIp(assignedIp)) throw new Error(`Server sent invalid IP: ${assignedIp}`);
      state.assignedIp = assignedIp;
      state.connected = true;
      state.error = null;
      backoff = BACKOFF_BASE; // Reset on success

      // Send hostname
      const os = require('node:os');
      state._ws.send(`HOST:${os.hostname()}`);

      log('Authenticated, assigned IP:', state.assignedIp);

      // Set up WS message handler EARLY (before TUN setup) so we don't miss PEERS broadcast
      state._ws.on('message', (data, isBinary) => {
        if (!isBinary) {
          const msg = data.toString();
          if (msg.startsWith('PEERS:')) {
            try { state.peers = JSON.parse(msg.slice(6)); } catch {}
          }
          return;
        }
        // Binary data = IP packet -> write to TUN
        if (data && data.length > 0 && tunSession) {
          const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
          tunWrite(buf);
          tunPktsRecv++;
        }
      });

      // Request peer list from server
      state._ws.send('GET_PEERS');

      // Setup TUN (once) — requires admin privileges
      if (!tunConfigured) {
        try {
          tunOpen('VibeVPN');
          tunConfigure(state.assignedIp, '10.8.0.1', 1400);
          setupRoutes(serverIp, gateway);
          setupDns();
          tunConfigured = true;
          log('TUN + routes + DNS configured successfully');
        } catch (tunErr) {
          state.error = `TUN adapter failed: ${tunErr.message}. Run as Administrator.`;
          state.connected = false;
          state.running = false;
          try { state._ws.close(); } catch {}
          log('TUN FAILED (need admin?):', tunErr.message);
          return;
        }
      }

      // Packet forwarding
      log('Starting packet forwarding...');

      // TUN -> WS (polling with WaitForSingleObject for efficiency)
      state._readInterval = setInterval(() => {
        if (!state.running || !tunSession) return;
        // Wait briefly for packet availability (non-blocking, 0ms timeout = just check)
        if (tunReadEvent) {
          wt.WaitForSingleObject(tunReadEvent, 0);
        }
        for (let i = 0; i < 128; i++) {
          const pkt = tunRead();
          if (!pkt) break;
          tunPktsSent++;
          if (state._ws && state._ws.readyState === WebSocket.OPEN) {
            state._ws.send(pkt);
          }
        }
      }, 1);

      // Log stats periodically
      const statsInterval = setInterval(() => {
        if (state.connected) {
          log(`Stats: sent=${tunPktsSent} recv=${tunPktsRecv} peers=${state.peers.length}`);
        }
      }, 10000);

      // Wait for connection to close
      await new Promise((resolve) => {
        state._ws.once('close', resolve);
        state._ws.once('error', resolve);
      });

      clearInterval(statsInterval);
      if (state._readInterval) { clearInterval(state._readInterval); state._readInterval = null; }

    } catch (e) {
      if (!state.running) break;
      state.connected = false;
      state.error = e.message;
      log(`Connection lost: ${e.message} - reconnecting in ${backoff}s...`);
      if (state._readInterval) { clearInterval(state._readInterval); state._readInterval = null; }
      await new Promise(r => setTimeout(r, backoff * 1000));
      backoff = Math.min(backoff * 2, BACKOFF_MAX);
    }
  }

  // Cleanup — each step in its own try/catch so failures don't cascade
  state.connected = false;
  if (state._readInterval) { clearInterval(state._readInterval); state._readInterval = null; }
  try { teardownRoutes(serverIp); } catch (e) { log('Failed to teardown routes:', e.message); }
  try { tunClose(); } catch (e) { log('Failed to close TUN:', e.message); }
  try { restoreDns(); } catch (e) { log('Failed to restore DNS:', e.message); }
  serverIp = null;
  log('Disconnected, cleanup done');
}

async function vpnDisconnect() {
  state.running = false;
  if (state._ws) {
    try { state._ws.close(); } catch {}
    state._ws = null;
  }
}

function vpnStatus() {
  const result = {
    connected: state.connected,
    server: state.server,
    assigned_ip: state.assignedIp,
    peers: state.peers,
    logs: _logs.slice(-30),
  };
  if (state.error) result.error = state.error;
  return result;
}

// ── Exports (used by main.js) ────────────────────────────────────────────

module.exports = { vpnConnect, vpnDisconnect, vpnStatus };
