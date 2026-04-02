const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, dialog } = require('electron');
const path = require('node:path');
const net = require('node:net');
const { spawn, spawnSync, execSync } = require('node:child_process');
const { existsSync, writeFileSync, copyFileSync, mkdirSync, chmodSync } = require('node:fs');

if (require('electron-squirrel-startup')) app.quit();

const IS_WIN = process.platform === 'win32';
const IS_MAC = process.platform === 'darwin';

// Single instance
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) { app.quit(); process.exit(0); }

let mainWindow = null;
let tray = null;

// Paths
const HELPER_DEST = '/Library/PrivilegedHelperTools/com.vibevpn.helper';
const HELPER_BIN = path.join(HELPER_DEST, 'vpn-helper');
const PLIST_DEST = '/Library/LaunchDaemons/com.vibevpn.helper.plist';
const HELPER_UNIX = '/tmp/vibevpn.sock';
const HELPER_TCP_PORT = 19876;

// ── Helper install (macOS) — runs once on first launch ────────────────

function isHelperInstalled() {
  if (!IS_MAC) return true;
  if (!existsSync(HELPER_BIN) || !existsSync(PLIST_DEST)) return false;

  // Check if bundled helper is newer than installed one
  const srcBin = app.isPackaged
    ? path.join(process.resourcesPath, 'vpn-helper')
    : path.resolve(__dirname, '..', '..', '..', 'MacOS', 'dist', 'vpn-helper');
  if (existsSync(srcBin)) {
    const { statSync } = require('node:fs');
    const installed = statSync(HELPER_BIN).size;
    const bundled = statSync(srcBin).size;
    if (installed !== bundled) return false;  // needs update
  }
  return true;
}

function isHelperRunning() {
  if (IS_MAC) return existsSync(HELPER_UNIX);
  // Windows: try TCP connect
  try {
    const s = new net.Socket();
    s.connect(HELPER_TCP_PORT, '127.0.0.1');
    s.destroy();
    return true;
  } catch { return false; }
}

function installHelper() {
  // vpn-helper binary is bundled in Resources/
  const srcBin = app.isPackaged
    ? path.join(process.resourcesPath, 'vpn-helper')
    : path.resolve(__dirname, '..', '..', '..', 'MacOS', 'dist', 'vpn-helper');
  const srcPlist = app.isPackaged
    ? path.join(process.resourcesPath, 'com.vibevpn.helper.plist')
    : path.resolve(__dirname, '..', '..', '..', 'MacOS', 'com.vibevpn.helper.plist');

  if (!existsSync(srcBin)) {
    dialog.showErrorBox('VibeVPN', `Helper binary not found at: ${srcBin}\nPlease rebuild the app.`);
    return false;
  }

  // Build install script
  const script = `
    mkdir -p '${HELPER_DEST}' && \
    cp '${srcBin}' '${HELPER_BIN}' && \
    chmod 755 '${HELPER_BIN}' && \
    cp '${srcPlist}' '${PLIST_DEST}' && \
    launchctl unload '${PLIST_DEST}' 2>/dev/null; \
    launchctl load -w '${PLIST_DEST}'
  `.trim().replace(/\n\s+/g, ' ');

  try {
    // Native macOS password dialog via osascript
    execSync(
      `osascript -e 'do shell script "${script.replace(/"/g, '\\"')}" with administrator privileges'`,
      { timeout: 60000 }
    );
    return true;
  } catch (err) {
    dialog.showErrorBox('VibeVPN', 'Failed to install helper. Administrator access is required.');
    return false;
  }
}

// ── Communication with helper (macOS) ─────────────────────────────────

function sendToHelper(cmd) {
  return new Promise((resolve, reject) => {
    const client = IS_MAC
      ? net.createConnection({ path: HELPER_UNIX })
      : net.createConnection({ host: '127.0.0.1', port: HELPER_TCP_PORT });

    client.setTimeout(10000);
    let buf = Buffer.alloc(0);

    client.on('connect', () => {
      const data = Buffer.from(JSON.stringify(cmd));
      const hdr = Buffer.alloc(4);
      hdr.writeUInt32BE(data.length);
      client.write(Buffer.concat([hdr, data]));
    });

    client.on('data', (chunk) => {
      buf = Buffer.concat([buf, chunk]);
      if (buf.length >= 4) {
        const len = buf.readUInt32BE(0);
        if (buf.length >= 4 + len) {
          try { resolve(JSON.parse(buf.slice(4, 4 + len).toString())); }
          catch { reject(new Error('Bad response')); }
          client.end();
        }
      }
    });

    client.on('error', reject);
    client.on('timeout', () => { client.destroy(); reject(new Error('Timeout')); });
  });
}

// ── Window ────────────────────────────────────────────────────────────

function createWindow() {
  if (mainWindow) { mainWindow.show(); mainWindow.focus(); return; }

  mainWindow = new BrowserWindow({
    width: 900, height: 620, minWidth: 800, minHeight: 560,
    titleBarStyle: IS_MAC ? 'hiddenInset' : 'default',
    ...(IS_MAC ? { trafficLightPosition: { x: 16, y: 16 } } : {}),
    backgroundColor: '#0f1117',
    show: false,
    webPreferences: {
      preload: MAIN_WINDOW_PRELOAD_WEBPACK_ENTRY,
      contextIsolation: true, nodeIntegration: false,
    },
  });

  mainWindow.loadURL(MAIN_WINDOW_WEBPACK_ENTRY);
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
      if (IS_MAC) app.dock.hide();
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── Tray ──────────────────────────────────────────────────────────────

function createTray() {
  const iconName = IS_MAC ? 'trayTemplate.png' : 'tray.png';
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, iconName)
    : path.join(__dirname, '..', '..', 'assets', iconName);

  const icon = existsSync(iconPath)
    ? nativeImage.createFromPath(iconPath)
    : nativeImage.createEmpty();

  if (IS_MAC) icon.setTemplateImage(true);

  tray = new Tray(icon);
  tray.setToolTip('VibeVPN');

  // Platform-aware helper calls for tray menu
  const getStatus = async () => {
    if (IS_WIN && vpnWin) return vpnWin.vpnStatus();
    return await sendToHelper({ action: 'status' });
  };
  const doDisconnect = async () => {
    if (IS_WIN && vpnWin) return await vpnWin.vpnDisconnect();
    return await sendToHelper({ action: 'disconnect' });
  };

  const updateMenu = async () => {
    let label = 'Disconnected';
    try {
      const s = await getStatus();
      if (s.connected) label = `Connected (${s.assigned_ip})`;
    } catch {}

    tray.setContextMenu(Menu.buildFromTemplate([
      { label: `VibeVPN — ${label}`, enabled: false },
      { type: 'separator' },
      { label: 'Open', click: () => { createWindow(); if (IS_MAC) app.dock.show(); } },
      ...(label.startsWith('Connected') ? [{
        label: 'Disconnect', click: async () => {
          try { await doDisconnect(); } catch {}
          updateMenu();
        }
      }] : []),
      { type: 'separator' },
      { label: 'Quit', click: async () => {
        try { await doDisconnect(); } catch {}
        app.isQuitting = true;
        app.quit();
      } },
    ]));
  };

  tray.on('click', () => {
    if (mainWindow?.isVisible()) mainWindow.focus();
    else { createWindow(); if (IS_MAC) app.dock.show(); }
  });

  updateMenu();
  setInterval(updateMenu, 5000);
}

// ── App lifecycle ─────────────────────────────────────────────────────

app.whenReady().then(async () => {
  // Hide menu bar on Windows (no File/Edit/View/Window/Help)
  if (IS_WIN) Menu.setApplicationMenu(null);

  // Install helper on first launch (one-time sudo)
  if (IS_MAC && !isHelperInstalled()) {
    const ok = installHelper();
    if (!ok) { app.quit(); return; }
    // Wait for helper to start
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 200));
      if (existsSync(HELPER_UNIX)) break;
    }
  }

  createTray();

  if (process.argv.includes('--hidden')) {
    if (IS_MAC) app.dock.hide();
  } else {
    createWindow();
  }
});

app.on('second-instance', () => {
  createWindow();
  if (IS_MAC) app.dock.show();
});

app.on('activate', () => createWindow());
app.on('window-all-closed', () => {}); // stay in tray
app.on('before-quit', () => { app.isQuitting = true; });

// Autostart
const firstRunKey = path.join(app.getPath('userData'), '.autostart-set');
if (!existsSync(firstRunKey)) {
  app.whenReady().then(() => {
    app.setLoginItemSettings({ openAtLogin: true, openAsHidden: true, args: ['--hidden'] });
    writeFileSync(firstRunKey, '1');
  });
}

// ── IPC ───────────────────────────────────────────────────────────────

// Windows: use integrated Node.js VPN module (no separate service)
let vpnWin = null;
if (IS_WIN) {
  try { vpnWin = require('./vpn-win'); }
  catch (err) { console.error('[VibeVPN] Failed to load vpn-win:', err); }
}
let vpnWinTask = null;

if (IS_WIN) {
  // Windows: direct calls to vpn-win.js
  ipcMain.handle('vpn:connect', async (_, { server, port, username, password }) => {
    try {
      const st = vpnWin.vpnStatus();
      if (st.connected) return { ok: true, ip: st.assigned_ip };

      // Prevent double-connect: if already running, return current error or wait
      if (vpnWinTask) {
        return { error: st.error || 'Connection already in progress' };
      }

      vpnWinTask = vpnWin.vpnConnect({ server, port: port || 443, username, password });
      // Clean up task reference when done
      vpnWinTask.finally(() => { vpnWinTask = null; });

      // Poll for connection
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 500));
        const s = vpnWin.vpnStatus();
        if (s.connected) return { ok: true, ip: s.assigned_ip };
        if (s.error) return { error: s.error };
      }
      return { error: 'Connection timeout' };
    } catch (err) {
      return { error: err.message };
    }
  });

  ipcMain.handle('vpn:disconnect', async () => {
    try { await vpnWin.vpnDisconnect(); } catch {}
    if (vpnWinTask) { try { await vpnWinTask; } catch {} vpnWinTask = null; }
    return { ok: true };
  });

  ipcMain.handle('vpn:status', async () => {
    return vpnWin.vpnStatus();
  });

  // Status polling
  setInterval(() => {
    if (!mainWindow) return;
    const s = vpnWin.vpnStatus();
    mainWindow.webContents.send('vpn:status-update', s);
    if (s.connected && s.peers) mainWindow.webContents.send('vpn:peers', s.peers);
  }, 1000);

} else {
  // macOS: communicate with helper over Unix socket (unchanged)
  ipcMain.handle('vpn:connect', async (_, { server, port, username, password }) => {
    try {
      const current = await sendToHelper({ action: 'status' });
      if (current.connected) return { ok: true, ip: current.assigned_ip };

      const result = await sendToHelper({
        action: 'connect', server, port: port || 443, username, password,
      });
      if (result.connected) return { ok: true, ip: result.assigned_ip };

      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 500));
        try {
          const st = await sendToHelper({ action: 'status' });
          if (st.connected) return { ok: true, ip: st.assigned_ip };
        } catch {}
      }
      return { error: result.error || 'Connection timeout' };
    } catch (err) {
      return { error: `Helper not running: ${err.message}` };
    }
  });

  ipcMain.handle('vpn:disconnect', async () => {
    try { await sendToHelper({ action: 'disconnect' }); } catch {}
    return { ok: true };
  });

  ipcMain.handle('vpn:status', async () => {
    try { return await sendToHelper({ action: 'status' }); }
    catch { return { connected: false }; }
  });

  setInterval(async () => {
    if (!mainWindow) return;
    try {
      const s = await sendToHelper({ action: 'status' });
      mainWindow.webContents.send('vpn:status-update', s);
      if (s.connected && s.peers) mainWindow.webContents.send('vpn:peers', s.peers);
    } catch {}
  }, 1000);
}
