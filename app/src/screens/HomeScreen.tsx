import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, FlatList, StyleSheet, Alert, StatusBar, SafeAreaView,
} from 'react-native';
import * as ExpoVpn from 'expo-vpn';
import { loadServers, saveServers, Server } from '../utils/storage';
import AddServerModal from '../components/AddServerModal';

const STATUS_COLORS: Record<string, string> = {
  disconnected: '#6b7280',
  connecting: '#f59e0b',
  connected: '#22c55e',
  disconnecting: '#f59e0b',
};

const STATUS_LABELS: Record<string, string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  connected: 'Connected',
  disconnecting: 'Disconnecting...',
};

export default function HomeScreen() {
  const [servers, setServers] = useState<Server[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('disconnected');
  const [showAdd, setShowAdd] = useState(false);
  const [peers, setPeers] = useState<any[]>([]);

  const selected = servers.find(s => s.id === selectedId) || null;

  useEffect(() => {
    loadServers().then(list => {
      setServers(list);
      if (list.length > 0) setSelectedId(list[0].id);
      if (list.length === 0) setShowAdd(true);
    });
  }, []);

  useEffect(() => {
    const sub = ExpoVpn.addStatusListener(({ status: s }) => {
      setStatus(s);
    });
    ExpoVpn.getStatus().then(s => {
      setStatus(s.status);
      if (s.peers) setPeers(s.peers);
    }).catch(() => {});
    return () => sub.remove();
  }, []);

  useEffect(() => {
    if (status !== 'connected') return;
    const timer = setInterval(() => {
      ExpoVpn.getStatus().then(s => {
        if (s.peers) setPeers(s.peers);
      }).catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [status]);

  const handleConnect = useCallback(async () => {
    if (!selected) return;

    if (status === 'connected' || status === 'connecting') {
      try { await ExpoVpn.disconnect(); } catch {}
      return;
    }

    try {
      await ExpoVpn.connect({
        host: selected.host,
        port: selected.port || 443,
        username: selected.username,
        password: selected.password,
      });
    } catch (err: any) {
      Alert.alert('Connection Error', err.message || 'Failed to connect');
    }
  }, [selected, status]);

  const handleAddServer = useCallback(async (server: Omit<Server, 'id'>) => {
    const newServer: Server = { ...server, id: Date.now().toString() };
    const updated = [...servers, newServer];
    setServers(updated);
    await saveServers(updated);
    setSelectedId(newServer.id);
    setShowAdd(false);
  }, [servers]);

  const handleRemoveServer = useCallback(async (id: string) => {
    const updated = servers.filter(s => s.id !== id);
    setServers(updated);
    await saveServers(updated);
    if (selectedId === id) setSelectedId(updated[0]?.id || null);
  }, [servers, selectedId]);

  const color = STATUS_COLORS[status] || STATUS_COLORS.disconnected;
  const label = STATUS_LABELS[status] || 'Disconnected';
  const btnLabel = status === 'connected' ? 'Disconnect' : status === 'connecting' ? 'Cancel' : 'Connect';

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="light-content" />

      <View style={styles.statusArea}>
        <View style={[styles.statusDot, { backgroundColor: color, shadowColor: color }]} />
        <Text style={[styles.statusText, { color }]}>{label}</Text>
        {status === 'connected' && <Text style={styles.statusSub}>Your traffic is protected</Text>}
      </View>

      <TouchableOpacity
        style={[styles.btn, { borderColor: color }]}
        onPress={handleConnect}
        disabled={!selected}
        activeOpacity={0.7}
      >
        <Text style={[styles.btnText, { color }]}>{btnLabel}</Text>
      </TouchableOpacity>

      <View style={styles.serverHeader}>
        <Text style={styles.sectionTitle}>Servers</Text>
        <TouchableOpacity onPress={() => setShowAdd(true)}>
          <Text style={styles.addBtn}>+</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        data={servers}
        keyExtractor={item => item.id}
        style={styles.list}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={[styles.serverItem, item.id === selectedId && styles.serverSelected]}
            onPress={() => setSelectedId(item.id)}
            onLongPress={() => {
              Alert.alert('Remove Server', `Remove ${item.name || item.host}?`, [
                { text: 'Cancel', style: 'cancel' },
                { text: 'Remove', style: 'destructive', onPress: () => handleRemoveServer(item.id) },
              ]);
            }}
          >
            <View style={[styles.dot, { backgroundColor: status === 'connected' && item.id === selectedId ? '#22c55e' : '#3a3f4b' }]} />
            <View style={styles.serverInfo}>
              <Text style={styles.serverName}>{item.name || item.host}</Text>
              <Text style={styles.serverHost}>{item.host}:{item.port || 443}</Text>
            </View>
          </TouchableOpacity>
        )}
        ListEmptyComponent={<Text style={styles.empty}>No servers. Tap + to add.</Text>}
      />

      {status === 'connected' && peers.length > 0 && (
        <View style={styles.peersSection}>
          <Text style={styles.sectionTitle}>Peers ({peers.length})</Text>
          {peers.map((p: any, i: number) => (
            <View key={p.ip || i} style={styles.peerItem}>
              <Text style={styles.peerName}>{p.hostname || p.username}</Text>
              <Text style={styles.peerIp}>{p.ip}</Text>
            </View>
          ))}
        </View>
      )}

      <AddServerModal
        visible={showAdd}
        onClose={() => setShowAdd(false)}
        onAdd={handleAddServer}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f1117', paddingHorizontal: 20 },
  statusArea: { alignItems: 'center', marginTop: 60, marginBottom: 30 },
  statusDot: { width: 60, height: 60, borderRadius: 30, marginBottom: 16, shadowOpacity: 0.4, shadowRadius: 20, elevation: 8 },
  statusText: { fontSize: 24, fontWeight: '700' },
  statusSub: { fontSize: 13, color: '#6b7280', marginTop: 4 },
  btn: { borderWidth: 2, borderRadius: 12, paddingVertical: 16, alignItems: 'center', marginBottom: 30 },
  btnText: { fontSize: 16, fontWeight: '600' },
  serverHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  sectionTitle: { fontSize: 16, fontWeight: '700', color: '#e4e7ed' },
  addBtn: { fontSize: 24, color: '#22c55e', paddingHorizontal: 8 },
  list: { flex: 1 },
  serverItem: { flexDirection: 'row', alignItems: 'center', padding: 14, borderRadius: 10, backgroundColor: '#1c2029', marginBottom: 8 },
  serverSelected: { backgroundColor: '#252a35', borderWidth: 1, borderColor: '#2a2f3a' },
  dot: { width: 10, height: 10, borderRadius: 5, marginRight: 12 },
  serverInfo: { flex: 1 },
  serverName: { fontSize: 15, fontWeight: '600', color: '#e4e7ed' },
  serverHost: { fontSize: 12, color: '#6b7280', marginTop: 2 },
  empty: { color: '#6b7280', textAlign: 'center', marginTop: 20 },
  peersSection: { paddingVertical: 12, borderTopWidth: 1, borderTopColor: '#2a2f3a' },
  peerItem: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 },
  peerName: { color: '#e4e7ed', fontSize: 13 },
  peerIp: { color: '#6b7280', fontSize: 13 },
});
