import React, { useState, useRef, useEffect } from 'react';

const STATUS_CONFIG = {
  disconnected: { label: 'Disconnected', color: '#6b7280', btnLabel: 'Connect', btnClass: 'btn-connect' },
  connecting:   { label: 'Connecting...', color: '#f59e0b', btnLabel: 'Cancel', btnClass: 'btn-cancel' },
  connected:    { label: 'Connected', color: '#22c55e', btnLabel: 'Disconnect', btnClass: 'btn-disconnect' },
};

export default function ConnectionPanel({ server, status, localIp, onConnect, logs }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.disconnected;
  const [logsOpen, setLogsOpen] = useState(false);
  const logEndRef = useRef(null);

  // Auto-scroll logs when new entries arrive
  useEffect(() => {
    if (logsOpen && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, logsOpen]);

  return (
    <div className="connection-panel">
      <div className="drag-region" />

      {/* Status */}
      <div className="status-area">
        <div className="status-dot-large" style={{ backgroundColor: cfg.color, boxShadow: `0 0 20px ${cfg.color}40` }} />
        <h1 className="status-text" style={{ color: cfg.color }}>{cfg.label}</h1>
        {server && status === 'connected' && (
          <p className="status-sub">Your traffic is protected</p>
        )}
        {!server && (
          <p className="status-sub">Add a server to get started</p>
        )}
      </div>

      {/* Connect button */}
      <button
        className={`btn-action ${cfg.btnClass}`}
        onClick={onConnect}
        disabled={!server}
      >
        {cfg.btnLabel}
      </button>

      {/* Server info */}
      {server && (
        <div className="server-info">
          <span className="server-info-label">{server.name || server.host}</span>
          <span className="server-info-detail">{server.host}:{server.port}</span>
        </div>
      )}

      {/* Bottom bar: Local IP + Logs toggle */}
      <div className="bottom-bar">
        {localIp && status === 'connected' && (
          <div className="local-ip">
            <span className="local-ip-label">Local IP</span>
            <span className="local-ip-value">{localIp}</span>
          </div>
        )}
        {(status !== 'disconnected' || logs.length > 0) && (
          <button
            className={`btn-logs ${logsOpen ? 'open' : ''}`}
            onClick={() => setLogsOpen(!logsOpen)}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" className={`logs-arrow ${logsOpen ? 'expanded' : ''}`}>
              <path d="M3 4.5L6 7.5L9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            Logs
          </button>
        )}
      </div>

      {/* Collapsible log area */}
      <div className={`log-panel ${logsOpen ? 'open' : ''}`}>
        <div className="log-content">
          {logs.length === 0 && (
            <div className="log-line" style={{ opacity: 0.4 }}>Connection logs will appear here</div>
          )}
          {logs.slice(-20).map((line, i) => (
            <div key={i} className="log-line">{line}</div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
