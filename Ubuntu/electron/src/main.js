const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, dialog } = require('electron');
const path = require('node:path');
const net = require('node:net');
const { spawn, spawnSync, execSync } = require('node:child_process');
const { existsSync, writeFileSync, copyFileSync, mkdirSync, chmodSync, readFileSync } = require('node:fs');

if (require('electron-squirrel-startup')) app.quit();

// Single instance
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) { app.quit(); process.exit(0); }

let mainWindow = null;
let tray = null;

// Paths (Linux)
const HELPER_DEST = '/opt/vibevpn';
const HELPER_BIN = path.join(HELPER_DEST, 'vpn-helper');
const SYSTEMD_UNIT = '/etc/systemd/system/vibevpn-helper.service';
const HELPER_UNIX = '/tmp/vibevpn.sock';

// ── Helper install (Linux) — runs once on first launch ───────────────

function isHelperInstalled() {
  if (!existsSync(HELPER_BIN) || !existsSync(SYSTEMD_UNIT)) return false;

  // Check if bundled helper is newer than installed one
  const srcBin = app.isPackaged
    ? path.join(process.resourcesPath, 'vpn-helper')
    : path.resolve(__dirname, '..', '..', '..', 'Ubuntu', 'dist', 'vpn-helper');
  if (existsSync(srcBin)) {
    const { statSync } = require('node:fs');
    try {
      const installed = statSync(HELPER_BIN).size;
      const bundled = statSync(srcBin).size;
      if (installed !== bundled) return false;  // needs update
    } catch { return false; }
  }
  return true;
}

function isHelperRunning() {
  return existsSync(HELPER_UNIX);
}

function installHelper() {
  // vpn-helper binary is bundled in Resources/
  const srcBin = app.isPackaged
    ? path.join(process.resourcesPath, 'vpn-helper')
    : path.resolve(__dirname, '..', '..', '..', 'Ubuntu', 'dist', 'vpn-helper');
  const srcUnit = app.isPackaged
    ? path.join(process.resourcesPath, 'vibevpn-helper.service')
    : path.resolve(__dirname, '..', '..', '..', 'Ubuntu', 'vibevpn-helper.service');

  if (!existsSync(srcBin)) {
    dialog.showErrorBox('VibeVPN', `Helper binary not found at: ${srcBin}\nPlease rebuild the app.`);
    return false;
  }

  // Build install script
  const script = [
    `mkdir -p '${HELPER_DEST}'`,
    `cp '${srcBin}' '${HELPER_BIN}'`,
    `chmod 755 '${HELPER_BIN}'`,
    `cp '${srcUnit}' '${SYSTEMD_UNIT}'`,
    `systemctl daemon-reload`,
    `systemctl enable vibevpn-helper.service`,
    `systemctl restart vibevpn-helper.service`,
  ].join(' && ');

  try {
    // Use pkexec for graphical sudo on Linux
    execSync(`pkexec sh -c "${script.replace(/"/g, '\\"')}"`, { timeout: 60000 });
    return true;
  } catch (err) {
    dialog.showErrorBox('VibeVPN', 'Failed to install helper. Administrator access is required.');
    return false;
  }
}

// ── Communication with helper ────────────────────────────────────────

function sendToHelper(cmd) {
  return new Promise((resolve, reject) => {
    const client = net.createConnection({ path: HELPER_UNIX });

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
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── Tray ──────────────────────────────────────────────────────────────

function createTray() {
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'tray.png')
    : path.join(__dirname, '..', '..', 'assets', 'tray.png');

  const icon = existsSync(iconPath)
    ? nativeImage.createFromPath(iconPath)
    : nativeImage.createEmpty();

  tray = new Tray(icon);
  tray.setToolTip('VibeVPN');

  const getStatus = async () => {
    return await sendToHelper({ action: 'status' });
  };
  const doDisconnect = async () => {
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
      { label: 'Open', click: () => createWindow() },
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
    else createWindow();
  });

  updateMenu();
  setInterval(updateMenu, 5000);
}

// ── App lifecycle ─────────────────────────────────────────────────────

app.whenReady().then(async () => {
  // Hide menu bar
  Menu.setApplicationMenu(null);

  // Install helper on first launch (one-time pkexec)
  if (!isHelperInstalled()) {
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
    // Start hidden in tray
  } else {
    createWindow();
  }
});

app.on('second-instance', () => createWindow());
app.on('activate', () => createWindow());
app.on('window-all-closed', () => {}); // stay in tray
app.on('before-quit', () => { app.isQuitting = true; });

// Autostart via .desktop file
const autostartDir = path.join(app.getPath('home'), '.config', 'autostart');
const autostartFile = path.join(autostartDir, 'vibevpn.desktop');
const firstRunKey = path.join(app.getPath('userData'), '.autostart-set');
if (!existsSync(firstRunKey)) {
  app.whenReady().then(() => {
    try {
      mkdirSync(autostartDir, { recursive: true });
      const exePath = process.execPath;
      writeFileSync(autostartFile, [
        '[Desktop Entry]',
        'Type=Application',
        'Name=VibeVPN',
        'Comment=VibeVPN Client',
        `Exec=${exePath} --hidden`,
        'X-GNOME-Autostart-enabled=true',
        'Hidden=false',
        '',
      ].join('\n'));
      writeFileSync(firstRunKey, '1');
    } catch (err) {
      console.error('Failed to set autostart:', err);
    }
  });
}

// ── IPC ───────────────────────────────────────────────────────────────

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
