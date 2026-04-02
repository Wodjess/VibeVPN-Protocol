import React, { useState } from 'react';

function PeerList({ peers }) {
  if (!peers || peers.length === 0) {
    return <div className="peer-empty">No peers connected</div>;
  }

  return (
    <div className="peer-list">
      {peers.map((p) => (
        <div key={p.ip} className="peer-item">
          <div className="peer-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="2" y="3" width="20" height="14" rx="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
          </div>
          <div className="peer-info">
            <span className="peer-hostname">{p.hostname || p.username}</span>
            <span className="peer-ip">{p.ip}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function ServerList({ servers, selectedId, connectedId, peers, onSelect, onRemove, onAdd }) {
  const [expandedId, setExpandedId] = useState(null);

  const toggleExpand = (id, e) => {
    e.stopPropagation();
    setExpandedId(expandedId === id ? null : id);
  };

  return (
    <div className="server-list">
      <div className="server-list-header">
        <h2>Servers</h2>
        <button className="btn-add" onClick={onAdd} title="Add server">+</button>
      </div>

      <div className="server-list-items">
        {servers.map((s) => {
          const isSelected = s.id === selectedId;
          const isConnected = s.id === connectedId;
          const isExpanded = isConnected && expandedId === s.id;

          return (
            <div key={s.id} className="server-item-wrapper">
              <div
                className={`server-item ${isSelected ? 'selected' : ''}`}
                onClick={() => onSelect(s.id)}
              >
                <div className="server-item-icon">
                  <div
                    className="server-dot"
                    style={{ backgroundColor: isConnected ? '#22c55e' : '#3a3f4b' }}
                  />
                </div>
                <div className="server-item-info">
                  <div className="server-item-name">{s.name || s.host}</div>
                  <div className="server-item-host">{s.host}:{s.port}</div>
                </div>
                {isConnected && (
                  <div className="server-item-right">
                    <div className="server-item-badge">Active</div>
                    <button
                      className="btn-expand"
                      onClick={(e) => toggleExpand(s.id, e)}
                      title="Show peers"
                    >
                      <svg
                        width="12" height="12" viewBox="0 0 12 12"
                        className={`expand-arrow ${isExpanded ? 'expanded' : ''}`}
                      >
                        <path d="M3 4.5L6 7.5L9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                      </svg>
                    </button>
                  </div>
                )}
                {!isConnected && (
                  <button
                    className="btn-remove"
                    onClick={(e) => { e.stopPropagation(); onRemove(s.id); }}
                    title="Remove"
                  >x</button>
                )}
              </div>

              {/* Expandable peer list */}
              <div className={`peer-panel ${isExpanded ? 'open' : ''}`}>
                <div className="peer-panel-header">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                    <circle cx="9" cy="7" r="4" />
                    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                  </svg>
                  <span>Network Peers ({peers.length})</span>
                </div>
                <PeerList peers={peers} />
              </div>
            </div>
          );
        })}

        {servers.length === 0 && (
          <div className="server-list-empty">
            No servers yet
          </div>
        )}
      </div>
    </div>
  );
}
