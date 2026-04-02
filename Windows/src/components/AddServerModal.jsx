import React, { useState } from 'react';

export default function AddServerModal({ onAdd, onClose }) {
  const [name, setName] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('443');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!host.trim()) { setError('Server IP is required'); return; }
    if (!username.trim()) { setError('Login is required'); return; }
    if (!password.trim()) { setError('Password is required'); return; }

    onAdd({
      name: name.trim() || host.trim(),
      host: host.trim(),
      port: parseInt(port, 10) || 443,
      username: username.trim(),
      password: password.trim(),
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">Add Server</h2>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Name (optional)</label>
            <input
              type="text" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="My VPN Server"
              autoFocus
            />
          </div>
          <div className="form-group">
            <label>Server IP</label>
            <input
              type="text" value={host} onChange={(e) => setHost(e.target.value)}
              placeholder="95.81.99.134"
            />
          </div>
          <div className="form-group">
            <label>Port</label>
            <input
              type="text" value={port} onChange={(e) => setPort(e.target.value)}
              placeholder="443"
            />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Login</label>
              <input
                type="text" value={username} onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
              />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input
                type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                placeholder="password"
              />
            </div>
          </div>

          {error && <div className="form-error">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary">Add</button>
          </div>
        </form>
      </div>
    </div>
  );
}
