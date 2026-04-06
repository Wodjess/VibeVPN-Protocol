/**
 * VibeVPN Windows — Standalone background VPN client (no Electron).
 * Uses koffi (FFI) to call wintun.dll, ws for WebSocket tunnel.
 * Run with: node vibevpn.js
 */

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { execSync, execFileSync } = require('node:child_process');
const WebSocket = require('ws');

// ── Config ──────────────────────────────────────────────────────────────

const CONFIG_PATH = path.join(__dirname, 'config.json');

function loadConfig() {
  if (!fs.existsSync(CONFIG_PATH)) {
    console.error('[VibeVPN] config.json not found at:', CONFIG_PATH);
    process.exit(1);
  }
  const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  if (!cfg.server || !cfg.username || !cfg.password) {
    console.error('[VibeVPN] config.json must have: server, username, password');
    process.exit(1);
  }
  cfg.port = cfg.port || 443;
  cfg.allowSelfSigned = cfg.allowSelfSigned !== false;
  return cfg;
}

// ── Helpers ─────────────────────────────────────────────────────────────

function isValidIp(s) {
  if (!/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(s)) return false;
  return s.split('.').every(n => { const v = parseInt(n, 10); return v >= 0 && v <= 255; });
}

// ── Logging with rotation ───────────────────────────────────────────────

const LOG_FILE = path.join(__dirname, 'vibevpn.log');
const LOG_MAX_SIZE = 5 * 1024 * 1024; // 5 MB

function log(...args) {
  const ts = new Date().toISOString().replace('T', ' ').slice(0, 19);
  const line = `[${ts}] ${args.join(' ')}`;
  console.log(line);
  try {
    // Rotate if log exceeds max size
    try {
      const stat = fs.statSync(LOG_FILE);
      if (stat.size > LOG_MAX_SIZE) {
        const old = LOG_FILE + '.old';
        if (fs.existsSync(old)) fs.unlinkSync(old);
        fs.renameSync(LOG_FILE, old);
      }
    } catch {}
    fs.appendFileSync(LOG_FILE, line + '\n');
  } catch {}
}

// ── Wintun via koffi ────────────────────────────────────────────────────

let koffi = null;
let wintunLib = null;
let kernel32 = null;
let wt = {};

function loadWintun() {
  if (wintunLib) return;
  koffi = require('koffi');

  const dllPath = path.join(__dirname, 'wintun.dll');
  if (!fs.existsSync(dllPath)) {
    throw new Error('wintun.dll not found at: ' + dllPath);
  }

  log('Loading wintun.dll');
  wintunLib = koffi.load(dllPath);
  kernel32 = koffi.load('kernel32.dll');

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

  log('wintun.dll loaded');
}

// ── TUN management ──────────────────────────────────────────────────────

let tunAdapter = null;
let tunSession = null;
let tunReadEvent = null;

function tunOpen(name) {
  loadWintun();
  log('Creating TUN adapter:', name);
  tunAdapter = wt.CreateAdapter(name, name, null);
  if (!tunAdapter) {
    const err = wt.GetLastError();
    const errMsg = err === 5 ? 'Access denied — run as Administrator'
                 : err === 577 ? 'Driver signature error — disable Secure Boot or use test signing'
                 : `WintunCreateAdapter failed (error ${err})`;
    throw new Error(errMsg);
  }
  tunSession = wt.StartSession(tunAdapter, 0x400000);
  if (!tunSession) {
    const err = wt.GetLastError();
    wt.CloseAdapter(tunAdapter);
    tunAdapter = null;
    throw new Error(`WintunStartSession failed (error ${err})`);
  }
  tunReadEvent = wt.GetReadWaitEvent(tunSession);
  log('TUN adapter ready');
}

function tunConfigure(ip, gateway, mtu) {
  if (!isValidIp(ip) || !isValidIp(gateway)) throw new Error(`Invalid IP: ${ip} / ${gateway}`);
  log('Configuring TUN:', ip, 'gw', gateway, 'mtu', mtu);
  try {
    execFileSync('netsh', ['interface', 'ip', 'set', 'address', 'name=VibeVPN', 'static', ip, '255.255.255.0', gateway],
      { windowsHide: true, timeout: 15000 });
  } catch (e) { log('netsh set address failed:', e.message); }
  try {
    execFileSync('netsh', ['interface', 'ipv4', 'set', 'subinterface', 'VibeVPN', `mtu=${mtu}`, 'store=persistent'],
      { windowsHide: true, timeout: 10000 });
  } catch {}
}

// Cache koffi array types to avoid recreating them for every packet
const _arrayTypeCache = new Map();
function getArrayType(size) {
  let t = _arrayTypeCache.get(size);
  if (!t) {
    t = koffi.array('uint8_t', size);
    _arrayTypeCache.set(size, t);
    // Keep cache bounded
    if (_arrayTypeCache.size > 256) {
      const first = _arrayTypeCache.keys().next().value;
      _arrayTypeCache.delete(first);
    }
  }
  return t;
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
  const arrType = getArrayType(size);
  const arr = koffi.decode(ptr, arrType);
  wt.ReleaseReceivePacket(tunSession, ptr);
  return Buffer.from(arr);
}

function tunWrite(data) {
  if (!tunSession || !data || data.length === 0) return;
  const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
  const ptr = wt.AllocateSendPacket(tunSession, buf.length);
  if (!ptr) return;
  const arrType = getArrayType(buf.length);
  koffi.encode(ptr, arrType, buf);
  wt.SendPacket(tunSession, ptr);
}

function tunClose() {
  if (tunSession) { try { wt.EndSession(tunSession); } catch {} tunSession = null; }
  if (tunAdapter) { try { wt.CloseAdapter(tunAdapter); } catch {} tunAdapter = null; }
  tunReadEvent = null;
}

// ── Route & DNS ─────────────────────────────────────────────────────────

let savedGateway = null; // real gateway, detected once before VPN routes

function getDefaultGateway() {
  // Return saved gateway if already detected (prevents picking VPN gateway on reconnect)
  if (savedGateway) {
    log('Using saved gateway:', savedGateway);
    return savedGateway;
  }
  try {
    // Exclude VibeVPN interface — only look at physical adapters
    const out = execSync(
      "powershell -NoProfile -Command \"Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Where-Object { (Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue).Name -ne 'VibeVPN' } | Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty NextHop\"",
      { windowsHide: true, timeout: 15000, encoding: 'utf8' }
    );
    const gw = out.trim();
    if (gw && isValidIp(gw) && !gw.startsWith('10.8.0.')) {
      savedGateway = gw;
      log('Detected real gateway:', savedGateway);
      return savedGateway;
    }
  } catch (e) { log('Gateway detection failed:', e.message); }
  // Fallback: parse 'route print' output
  try {
    const out = execSync('route print 0.0.0.0', { windowsHide: true, timeout: 5000, encoding: 'utf8' });
    const match = out.match(/0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)/);
    if (match && !match[1].startsWith('10.8.0.')) {
      savedGateway = match[1];
      log('Detected gateway from route print:', savedGateway);
      return savedGateway;
    }
  } catch {}
  savedGateway = '192.168.1.1';
  log('Using fallback gateway:', savedGateway);
  return savedGateway;
}

let currentServerRoute = null; // track which server IP route we added

function setupRoutes(serverIp, gateway) {
  if (!isValidIp(serverIp) || !isValidIp(gateway)) return;
  log('Setting routes: server', serverIp, 'via', gateway);
  const run = (args, label) => {
    try {
      execFileSync('route', args, { windowsHide: true, timeout: 5000 });
      log('Route OK:', label);
    } catch (e) {
      log('Route FAILED:', label, '-', e.message);
    }
  };
  // Clean old server route if IP changed
  if (currentServerRoute && currentServerRoute !== serverIp) {
    run(['delete', currentServerRoute], `delete old ${currentServerRoute}`);
  }
  run(['add', serverIp, 'mask', '255.255.255.255', gateway, 'metric', '5'], `${serverIp} via ${gateway}`);
  run(['add', '0.0.0.0', 'mask', '128.0.0.0', '10.8.0.1', 'metric', '5'], '0.0.0.0/1 via 10.8.0.1');
  run(['add', '128.0.0.0', 'mask', '128.0.0.0', '10.8.0.1', 'metric', '5'], '128.0.0.0/1 via 10.8.0.1');
  currentServerRoute = serverIp;

  // Log routing table for diagnostics
  try {
    const table = execSync('route print 0.0.0.0', { windowsHide: true, timeout: 5000, encoding: 'utf8' });
    log('Route table (0.0.0.0):\n' + table.trim());
  } catch {}
}

function teardownRoutes() {
  const run = (args) => { try { execFileSync('route', args, { windowsHide: true, timeout: 5000 }); } catch {} };
  run(['delete', '0.0.0.0', 'mask', '128.0.0.0']);
  run(['delete', '128.0.0.0', 'mask', '128.0.0.0']);
  if (currentServerRoute) { run(['delete', currentServerRoute]); currentServerRoute = null; }
}

// IPv6: disable on all adapters while VPN is active (prevents IPv6 leak)
let savedIpv6States = null;

function disableIpv6() {
  try {
    const out = execSync(
      'powershell -NoProfile -Command "Get-NetAdapterBinding -ComponentId ms_tcpip6 | Where-Object { $_.Enabled -eq $true } | Select-Object Name | ConvertTo-Json -Compress"',
      { windowsHide: true, timeout: 15000, encoding: 'utf8' }
    );
    const parsed = JSON.parse(out.trim());
    savedIpv6States = Array.isArray(parsed) ? parsed : [parsed];
    log('Disabling IPv6 on', savedIpv6States.length, 'adapter(s)');
    execSync(
      'powershell -NoProfile -Command "Get-NetAdapterBinding -ComponentId ms_tcpip6 | Where-Object { $_.Enabled -eq $true } | Disable-NetAdapterBinding -ComponentId ms_tcpip6 -Confirm:$false"',
      { windowsHide: true, timeout: 30000 });
    log('IPv6 disabled');
  } catch (e) { log('Failed to disable IPv6:', e.message); }
}

function restoreIpv6() {
  if (savedIpv6States) {
    for (const adapter of savedIpv6States) {
      try {
        execSync(
          `powershell -NoProfile -Command "Enable-NetAdapterBinding -Name '${adapter.Name}' -ComponentId ms_tcpip6 -Confirm:$false"`,
          { windowsHide: true, timeout: 10000 });
      } catch {}
    }
    savedIpv6States = null;
    log('IPv6 restored');
  }
}

// DNS: save original settings before changing, restore on exit
let savedDnsAdapters = null;

function saveDns() {
  try {
    const out = execSync(
      'powershell -NoProfile -Command "Get-NetAdapter | Where-Object { $_.Status -eq \'Up\' -and $_.Name -ne \'VibeVPN\' } | Get-DnsClientServerAddress -AddressFamily IPv4 | ConvertTo-Json -Compress"',
      { windowsHide: true, timeout: 15000, encoding: 'utf8' }
    );
    const parsed = JSON.parse(out.trim());
    savedDnsAdapters = Array.isArray(parsed) ? parsed : [parsed];
    log('Saved DNS settings for', savedDnsAdapters.length, 'adapter(s)');
  } catch (e) { log('Failed to save DNS:', e.message); }
}

function setupDns() {
  saveDns();
  log('Setting DNS to 1.1.1.1');
  try {
    execFileSync('powershell', ['-NoProfile', '-Command',
      "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -ne 'VibeVPN' } | Set-DnsClientServerAddress -ServerAddresses 1.1.1.1"],
      { windowsHide: true, timeout: 30000 });
  } catch (e) { log('DNS set failed:', e.message); }
}

function restoreDns() {
  if (savedDnsAdapters) {
    // Restore each adapter to its original DNS
    for (const adapter of savedDnsAdapters) {
      try {
        const idx = adapter.InterfaceIndex;
        const addrs = adapter.ServerAddresses;
        if (addrs && addrs.length > 0) {
          const addrStr = addrs.join(',');
          execSync(
            `powershell -NoProfile -Command "Set-DnsClientServerAddress -InterfaceIndex ${idx} -ServerAddresses ${addrStr}"`,
            { windowsHide: true, timeout: 10000 });
        } else {
          execSync(
            `powershell -NoProfile -Command "Set-DnsClientServerAddress -InterfaceIndex ${idx} -ResetServerAddresses"`,
            { windowsHide: true, timeout: 10000 });
        }
      } catch {}
    }
    savedDnsAdapters = null;
    log('DNS restored to original settings');
  } else {
    // Fallback: reset all
    try {
      execSync(
        "powershell -NoProfile -Command \"Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -ne 'VibeVPN' } | Set-DnsClientServerAddress -ResetServerAddresses\"",
        { windowsHide: true, timeout: 15000 });
      log('DNS reset to DHCP');
    } catch {}
  }
}

// ── Resolve hostname ────────────────────────────────────────────────────

function resolveHost(host) {
  // Skip DNS lookup for raw IP addresses
  if (isValidIp(host)) return Promise.resolve(host);
  const { lookup } = require('node:dns');
  return new Promise((resolve, reject) => {
    lookup(host, 4, (err, addr) => err ? reject(err) : resolve(addr));
  });
}

// ── VPN Connect (main loop) ─────────────────────────────────────────────

let running = true;
let currentWs = null;
let resolvedServerIp = null;

async function vpnConnect(config) {
  const { server, port, username, password, allowSelfSigned } = config;
  let tunPktsSent = 0, tunPktsRecv = 0;

  log('Connecting to', server + ':' + port);

  let lastAssignedIp = null;

  while (running) {
    let tunReadActive = false;
    try {
      // Re-resolve DNS and re-detect gateway on every reconnect
      try {
        resolvedServerIp = await resolveHost(server);
        log('Resolved', server, '->', resolvedServerIp);
      } catch (e) {
        log('DNS failed:', e.message, '- retrying in 3s...');
        await new Promise(r => setTimeout(r, 3000));
        continue;
      }

      const gateway = getDefaultGateway();

      const wsUrl = `wss://${server}:${port}`;
      const wsOpts = allowSelfSigned ? { rejectUnauthorized: false } : {};

      log('WebSocket connecting to', wsUrl);
      currentWs = new WebSocket(wsUrl, wsOpts);

      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Connection timeout')), 15000);
        currentWs.once('open', () => { clearTimeout(timer); resolve(); });
        currentWs.once('error', (e) => { clearTimeout(timer); reject(e); });
      });

      log('Authenticating...');
      currentWs.send(`${username}:${password}`);
      const response = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Auth timeout')), 10000);
        currentWs.once('message', (data) => { clearTimeout(timer); resolve(data.toString()); });
      });

      const assignedIp = response.trim();
      if (!isValidIp(assignedIp)) throw new Error(`Server sent invalid IP: ${assignedIp}`);

      currentWs.send(`HOST:${os.hostname()}`);
      log('Authenticated, IP:', assignedIp);

      // Handle incoming messages
      currentWs.on('message', (data, isBinary) => {
        if (!isBinary) {
          const msg = data.toString();
          if (msg.startsWith('PEERS:')) {
            try {
              const peers = JSON.parse(msg.slice(6));
              log('Peers:', peers.map(p => `${p.hostname || p.username} (${p.ip})`).join(', '));
            } catch {}
          }
          return;
        }
        if (data && data.length > 0 && tunSession) {
          const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
          tunWrite(buf);
          tunPktsRecv++;
        }
      });

      currentWs.send('GET_PEERS');

      // Setup or reconfigure TUN if IP changed
      if (!tunAdapter) {
        tunOpen('VibeVPN');
      }
      if (assignedIp !== lastAssignedIp) {
        tunConfigure(assignedIp, '10.8.0.1', 1400);
        lastAssignedIp = assignedIp;
      }
      // Order matters: disable IPv6 FIRST (may cause adapter flicker),
      // wait for adapters to stabilize, THEN set routes.
      if (!savedIpv6States) {
        disableIpv6();
        // Wait for adapters to stabilize after IPv6 unbind
        execSync('ping -n 2 127.0.0.1 >nul', { windowsHide: true, timeout: 5000 });
      }
      if (!savedDnsAdapters) setupDns();
      setupRoutes(resolvedServerIp, gateway);
      log('TUN + routes + DNS ready');

      log('VPN connected. Forwarding packets...');

      // TUN -> WS: async loop that yields to event loop between batches
      // This prevents blocking WebSocket ping/pong and incoming message processing
      tunReadActive = true;
      const WS_BACKPRESSURE_LIMIT = 1024 * 1024; // 1MB buffer limit
      const tunReadLoop = () => {
        if (!tunReadActive || !running || !tunSession) return;

        // Skip if WebSocket send buffer is too full (backpressure)
        if (currentWs && currentWs.bufferedAmount > WS_BACKPRESSURE_LIMIT) {
          setTimeout(tunReadLoop, 5);
          return;
        }

        // Wait briefly for packets (non-blocking check)
        if (tunReadEvent) wt.WaitForSingleObject(tunReadEvent, 0);

        let readCount = 0;
        for (let i = 0; i < 32; i++) {
          const pkt = tunRead();
          if (!pkt) break;
          readCount++;
          tunPktsSent++;
          if (currentWs && currentWs.readyState === WebSocket.OPEN) {
            currentWs.send(pkt);
          }
        }

        // Adaptive scheduling: if we read packets, come back soon;
        // if idle, back off to save CPU and let event loop breathe
        if (readCount > 0) {
          setImmediate(tunReadLoop);
        } else {
          setTimeout(tunReadLoop, 2);
        }
      };
      setImmediate(tunReadLoop);

      const statsInterval = setInterval(() => {
        log(`Stats: sent=${tunPktsSent} recv=${tunPktsRecv}`);
      }, 30000);

      // Wait for disconnect
      await new Promise((resolve) => {
        currentWs.once('close', resolve);
        currentWs.once('error', resolve);
      });

      clearInterval(statsInterval);
      tunReadActive = false;

    } catch (e) {
      if (!running) break;
      log('Connection lost:', e.message, '- reconnecting in 3s...');
      tunReadActive = false;
      await new Promise(r => setTimeout(r, 3000));
    }
  }

  // Cleanup
  cleanup();
  log('Disconnected, cleanup done');
}

// ── Graceful shutdown ───────────────────────────────────────────────────

let cleanupDone = false;

function cleanup() {
  if (cleanupDone) return;
  cleanupDone = true;
  try { teardownRoutes(); } catch {}
  try { tunClose(); } catch {}
  try { restoreDns(); } catch {}
  try { restoreIpv6(); } catch {}
}

function shutdown() {
  log('Shutting down...');
  running = false;
  if (currentWs) { try { currentWs.close(); } catch {} currentWs = null; }
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
process.on('SIGHUP', shutdown);
process.on('uncaughtException', (e) => { log('Uncaught exception:', e.message); cleanup(); process.exit(1); });
process.on('exit', () => cleanup());

// Windows: handle Ctrl+C in console
if (process.platform === 'win32') {
  const rl = require('node:readline').createInterface({ input: process.stdin });
  rl.on('SIGINT', shutdown);
}

// ── Start ───────────────────────────────────────────────────────────────

const config = loadConfig();
log('VibeVPN starting —', config.server + ':' + config.port, 'as', config.username);
vpnConnect(config).catch((e) => {
  log('Fatal error:', e.message);
  process.exit(1);
});
