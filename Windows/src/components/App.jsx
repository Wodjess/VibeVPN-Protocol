import React, { useState, useEffect, useCallback } from 'react';
import ConnectionPanel from './ConnectionPanel.jsx';
import ServerList from './ServerList.jsx';
import AddServerModal from './AddServerModal.jsx';

const STORAGE_KEY = 'vibevpn_servers';

function loadServers() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch { return []; }
}

function saveServers(servers) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(servers));
}

export default function App() {
  const [servers, setServers] = useState(loadServers);
  const [selectedId, setSelectedId] = useState(() => {
    const saved = loadServers();
    return saved.length > 0 ? saved[0].id : null;
  });
  const [status, setStatus] = useState('disconnected'); // disconnected | connecting | connected
  const [logs, setLogs] = useState([]);
  const [showAdd, setShowAdd] = useState(() => loadServers().length === 0);
  const [connectedServerId, setConnectedServerId] = useState(null);
  const [peers, setPeers] = useState([]);
  const [localIp, setLocalIp] = useState(null);

  const selected = servers.find((s) => s.id === selectedId) || null;

  // Realtime status sync from helper (1s polling via main process)
  useEffect(() => {
    const offStatus = window.vpn.onStatusUpdate((st) => {
      // Always update logs and peers
      if (st.logs && st.logs.length > 0) setLogs(st.logs);
      if (st.peers) setPeers(st.peers);

      if (st.connected) {
        setStatus('connected');
        setLocalIp(st.assigned_ip || null);
        const match = servers.find((s) => s.host === st.server) || servers[0];
        if (match && connectedServerId !== match.id) {
          setSelectedId(match.id);
          setConnectedServerId(match.id);
        }
      } else if (status === 'connected' || status === 'connecting') {
        setStatus('disconnected');
        setConnectedServerId(null);
        setLocalIp(null);
      }
    });

    // Also check once immediately on mount
    window.vpn.status().then((st) => {
      if (st.connected) {
        setStatus('connected');
        setLocalIp(st.assigned_ip || null);
        if (st.peers) setPeers(st.peers);
        if (st.logs) setLogs(st.logs);
        const match = servers.find((s) => s.host === st.server) || servers[0];
        if (match) { setSelectedId(match.id); setConnectedServerId(match.id); }
      }
    }).catch(() => {});

    return () => offStatus();
  }, [servers]);

  // Listen for peer list pushes
  useEffect(() => {
    const offPeers = window.vpn.onPeers((peerList) => {
      setPeers(peerList || []);
    });
    return () => offPeers();
  }, []);

  // Show add modal on first launch
  useEffect(() => {
    if (servers.length === 0) setShowAdd(true);
  }, []);

  const handleAddServer = useCallback((server) => {
    const newServer = { ...server, id: Date.now().toString() };
    const updated = [...servers, newServer];
    setServers(updated);
    saveServers(updated);
    setSelectedId(newServer.id);
    setShowAdd(false);
  }, [servers]);

  const handleRemoveServer = useCallback((id) => {
    const updated = servers.filter((s) => s.id !== id);
    setServers(updated);
    saveServers(updated);
    if (selectedId === id) setSelectedId(updated[0]?.id || null);
  }, [servers, selectedId]);

  const handleConnect = useCallback(async () => {
    if (!selected) return;

    if (status === 'connected' || status === 'connecting') {
      setStatus('disconnected');
      setConnectedServerId(null);
      try { await window.vpn.disconnect(); } catch {}
      return;
    }

    setStatus('connecting');
    setLogs([]);
    setPeers([]);
    setLocalIp(null);

    try {
      const result = await window.vpn.connect({
        server: selected.host,
        port: selected.port,
        username: selected.username,
        password: selected.password,
      });

      if (result && result.error) {
        setStatus('disconnected');
        setLogs((prev) => [...prev, `Error: ${result.error}`]);
      } else if (result) {
        setStatus('connected');
        setConnectedServerId(selected.id);
        setLocalIp(result.ip || null);
      } else {
        setStatus('disconnected');
        setLogs((prev) => [...prev, 'Error: No response from service']);
      }
    } catch (err) {
      setStatus('disconnected');
      setLogs((prev) => [...prev, `Error: ${err.message || err}`]);
    }
  }, [selected, status]);

  return (
    <div className="app">
      {/* Left panel */}
      <div className="panel-left">
        <div className="panel-left-inner">
          <ConnectionPanel
            server={selected}
            status={status === 'connecting' ? 'connecting' : (connectedServerId === selectedId ? status : 'disconnected')}
            localIp={localIp}
            onConnect={handleConnect}
            logs={logs}
          />
        </div>
      </div>

      {/* Right panel */}
      <div className="panel-right">
        <ServerList
          servers={servers}
          selectedId={selectedId}
          connectedId={connectedServerId}
          peers={peers}
          onSelect={setSelectedId}
          onRemove={handleRemoveServer}
          onAdd={() => setShowAdd(true)}
        />
      </div>

      {/* Add server modal */}
      {showAdd && (
        <AddServerModal
          onAdd={handleAddServer}
          onClose={() => { if (servers.length > 0) setShowAdd(false); }}
        />
      )}
    </div>
  );
}
