const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vpn', {
  connect: (opts) => ipcRenderer.invoke('vpn:connect', opts),
  disconnect: () => ipcRenderer.invoke('vpn:disconnect'),
  status: () => ipcRenderer.invoke('vpn:status'),
  onPeers: (cb) => {
    const handler = (_, peers) => cb(peers);
    ipcRenderer.on('vpn:peers', handler);
    return () => ipcRenderer.removeListener('vpn:peers', handler);
  },
  onStatusUpdate: (cb) => {
    const handler = (_, st) => cb(st);
    ipcRenderer.on('vpn:status-update', handler);
    return () => ipcRenderer.removeListener('vpn:status-update', handler);
  },
});
